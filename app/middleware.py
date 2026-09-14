"""FastAPI middleware for observability (Phase 5).

- HTTP request metrics (Prometheus)
- Correlation ID injection for distributed tracing
"""

import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.metrics import http_request_duration_seconds, http_requests_total

# Correlation ID — propagated through logs and traces for a single request.
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the current request's correlation ID (or empty if none)."""
    return correlation_id_var.get()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP request count and latency to Prometheus."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip metrics for /metrics and /health to avoid noise
        if request.url.path in ("/metrics", "/health"):
            return await call_next(request)

        method = request.method
        start = time.perf_counter()

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            # Unhandled exception → record 500 before re-raising.
            # ServerErrorMiddleware above us will catch it and return 500 to client,
            # but we must record the metric HERE or it's invisible to Prometheus.
            duration = time.perf_counter() - start
            endpoint = self._get_endpoint(request)
            http_requests_total.labels(method=method, endpoint=endpoint, status="500").inc()
            http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)
            raise

        duration = time.perf_counter() - start
        endpoint = self._get_endpoint(request)

        http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status=str(status),
        ).inc()

        http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
        ).observe(duration)

        return response

    @staticmethod
    def _get_endpoint(request: Request) -> str:
        """Get endpoint label from matched route, or 'unmatched' for 404s.

        Uses the route template (e.g., '/rooms/{room_id}') instead of raw path.
        Prevents cardinality explosion from scanner probes hitting random URLs.
        """
        route = request.scope.get("route")
        if route is not None:
            return route.path
        return "unmatched"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject or propagate a correlation ID for distributed tracing."""

    HEADER = "X-Correlation-ID"

    async def dispatch(self, request: Request, call_next) -> Response:
        # Use existing header or generate a new UUID
        corr_id = request.headers.get(self.HEADER) or str(uuid.uuid4())
        correlation_id_var.set(corr_id)

        response = await call_next(request)
        response.headers[self.HEADER] = corr_id
        return response
