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

        # Normalize endpoint path (strip IDs for cardinality control)
        endpoint = self._normalize_path(request.url.path)
        method = request.method

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status=str(response.status_code),
        ).inc()

        http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
        ).observe(duration)

        return response

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Replace numeric path segments with {id} to limit label cardinality."""
        parts = path.strip("/").split("/")
        normalized = []
        for part in parts:
            if part.isdigit():
                normalized.append("{id}")
            else:
                normalized.append(part)
        return "/" + "/".join(normalized) if normalized else "/"


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
