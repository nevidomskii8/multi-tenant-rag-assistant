"""Observability tests — Phase 5 DoD (metrics, rate limiting, middleware).

Tests the Prometheus metrics endpoint, rate limiting behavior, and middleware
(correlation ID, metrics collection). No external services required: Redis is
skipped (in-memory fallback), Prometheus/Loki/Langfuse are not needed for
unit tests.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.middleware import correlation_id_var

client = TestClient(app)


# --- /metrics endpoint ---------------------------------------------------------


def test_metrics_endpoint_returns_prometheus_format():
    """The /metrics endpoint returns Prometheus-compatible text."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # Must contain some standard Prometheus metric
    assert b"http_requests_total" in resp.content or b"python_info" in resp.content


def test_metrics_endpoint_includes_app_metrics():
    """Custom app metrics are exposed on /metrics after requests are made."""
    # First make a health request to trigger some metrics
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.content.decode()
    # http_request_duration_seconds should be recorded
    assert "http_request_duration_seconds" in body


# --- Correlation ID middleware --------------------------------------------------


def test_correlation_id_generated_when_not_provided():
    """A correlation ID is generated if not in request headers."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "X-Correlation-ID" in resp.headers
    corr_id = resp.headers["X-Correlation-ID"]
    assert len(corr_id) > 0


def test_correlation_id_preserved_when_provided():
    """A provided X-Correlation-ID is echoed back."""
    test_id = "test-correlation-12345"
    resp = client.get("/health", headers={"X-Correlation-ID": test_id})
    assert resp.status_code == 200
    assert resp.headers.get("X-Correlation-ID") == test_id


# --- Rate limiting (in-memory fallback, no Redis) --------------------------------


def _make_mock_request(user_id=None):
    """Create a mock request object with all required attributes."""
    return type(
        "MockRequest",
        (),
        {
            "url": type("URL", (), {"path": "/chat"})(),
            "state": type("State", (), {"user_id": user_id})(),
            "headers": {},
            "client": type("Client", (), {"host": "192.168.1.1"})(),
        },
    )()


def test_rate_limit_handler_returns_429():
    """Rate limit handler returns proper 429 response."""
    from app.ratelimit import rate_limit_exceeded_handler
    import json

    mock_request = _make_mock_request()

    # Create a mock exception with the detail attribute
    mock_exc = type(
        "MockRateLimitExceeded",
        (Exception,),
        {"detail": "Rate limit exceeded: 10 per 1 minute"},
    )()

    resp = rate_limit_exceeded_handler(mock_request, mock_exc)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    body = json.loads(resp.body)
    assert body["detail"] == "Rate limit exceeded. Please slow down."


def test_rate_limit_handler_format():
    """Rate limit response has correct JSON structure."""
    from app.ratelimit import rate_limit_exceeded_handler
    import json

    mock_request = _make_mock_request()

    # Create a mock exception with the detail attribute
    mock_exc = type(
        "MockRateLimitExceeded",
        (Exception,),
        {"detail": "Rate limit exceeded: 60 per 1 minute"},
    )()
    resp = rate_limit_exceeded_handler(mock_request, mock_exc)

    assert resp.status_code == 429
    body = json.loads(resp.body)
    assert "detail" in body
    assert "retry_after" in body
    assert resp.headers.get("Retry-After") is not None
    # Should extract 60 from the detail message
    assert body["retry_after"] == 60


# --- Metrics recording ----------------------------------------------------------


def test_http_metrics_defined():
    """HTTP metrics are properly defined with labels."""
    from app.metrics import http_requests_total, http_request_duration_seconds

    # Check that metrics have the expected labels
    assert "method" in http_requests_total._labelnames
    assert "endpoint" in http_requests_total._labelnames
    assert "status" in http_requests_total._labelnames

    assert "method" in http_request_duration_seconds._labelnames
    assert "endpoint" in http_request_duration_seconds._labelnames


def test_llm_metrics_defined():
    """LLM metrics are properly defined."""
    from app.metrics import (
        llm_requests_total,
        llm_tokens_total,
        llm_cost_dollars_total,
        estimate_cost,
    )

    # Check labels — room_id removed to prevent tenant structure leakage
    assert "model" in llm_requests_total._labelnames
    assert "room_id" not in llm_requests_total._labelnames  # security: no tenant labels
    assert "direction" in llm_tokens_total._labelnames

    # Test cost estimation
    cost = estimate_cost("claude-haiku-4-5", 1000, 500)
    assert cost > 0
    assert isinstance(cost, float)


# --- Logging configuration ------------------------------------------------------


def test_logging_config_runs_without_error():
    """Logging configuration can be called multiple times without error."""
    from app.logging_config import configure_logging, get_logger

    # Should not raise
    configure_logging()
    log = get_logger("test")
    assert log is not None


def test_get_logger_returns_bound_logger():
    """get_logger returns a structlog BoundLogger."""
    from app.logging_config import get_logger
    import structlog

    log = get_logger("test_module")
    assert hasattr(log, "info")
    assert hasattr(log, "error")
    assert hasattr(log, "warning")


# --- Tracing (no-op when Langfuse not configured) --------------------------------


def test_tracing_noop_when_disabled():
    """Tracing context manager works as no-op when Langfuse is not configured."""
    from app.tracing import trace_generation, is_enabled

    # In test env, Langfuse should not be configured
    # The context manager should work without error
    with trace_generation("test", "claude-haiku", "test prompt") as trace:
        trace.update(output="test output", usage={"input": 10, "output": 20})
        # Only call score if trace is not a no-op (no-op doesn't need testing)
        if hasattr(trace, "score"):
            trace.score(name="test_score", value=0.95)


def test_tracing_is_enabled_returns_bool():
    """is_enabled returns a boolean."""
    from app.tracing import is_enabled

    result = is_enabled()
    assert isinstance(result, bool)


# --- Rate limiter key function ---------------------------------------------------


def test_rate_limiter_key_extracts_user_from_jwt():
    """Rate limiter key function extracts user_id from JWT token."""
    from app.ratelimit import _get_user_id_or_ip
    from app.auth import create_access_token

    # Create a valid token
    token = create_access_token(123, "test@example.com")

    mock_request = type(
        "MockRequest",
        (),
        {
            "state": type("State", (), {})(),
            "headers": {"Authorization": f"Bearer {token}"},
        },
    )()

    key = _get_user_id_or_ip(mock_request)
    assert key == "user:123"


def test_rate_limiter_key_falls_back_to_ip():
    """Rate limiter key function falls back to IP when no valid token."""
    from app.ratelimit import _get_user_id_or_ip

    mock_request = type(
        "MockRequest",
        (),
        {
            "state": type("State", (), {})(),
            "headers": {},
            "client": type("Client", (), {"host": "192.168.1.1"})(),
            "scope": {"type": "http"},
        },
    )()

    key = _get_user_id_or_ip(mock_request)
    assert key.startswith("ip:")
