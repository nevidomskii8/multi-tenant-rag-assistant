"""Rate limiting for denial-of-wallet defense (Phase 5, LLM10).

Uses slowapi with Redis backend for distributed rate limiting.
Per-user limits on /chat endpoint to prevent unbounded API cost.
"""

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings
from app.metrics import rate_limit_hits_total


def _get_user_id_or_ip(request: Request) -> str:
    """Extract user ID from JWT or fall back to IP for rate limit key.

    Authenticated users are limited per-user; unauthenticated requests
    (e.g., /auth/*) are limited per-IP.
    """
    # Check if we have a user_id set by the auth dependency
    if hasattr(request.state, "user_id") and request.state.user_id:
        return f"user:{request.state.user_id}"

    # Try to extract user_id from JWT Authorization header
    # (rate limiter runs before FastAPI dependencies, so we parse directly)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            import jwt

            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={"verify_exp": True},
            )
            user_id = payload.get("user_id")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass  # Invalid token — fall back to IP

    # Fall back to IP address
    return f"ip:{get_remote_address(request)}"


def _create_limiter() -> Limiter:
    """Create the slowapi limiter with Redis storage if available."""
    storage_uri = settings.redis_url or None

    return Limiter(
        key_func=_get_user_id_or_ip,
        storage_uri=storage_uri,
        strategy="fixed-window",
        # In-memory fallback when Redis is unavailable
        in_memory_fallback_enabled=True,
    )


limiter = _create_limiter()


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Custom handler for rate limit exceeded errors.

    Records metrics and returns a structured JSON response with Retry-After header.
    """
    # Extract user identifier for metrics
    key = _get_user_id_or_ip(request)
    endpoint = request.url.path

    # Record metric
    rate_limit_hits_total.labels(user_id=key, endpoint=endpoint).inc()

    # Parse retry-after from the exception
    retry_after = 60  # default
    if hasattr(exc, "detail") and "retry after" in str(exc.detail).lower():
        try:
            # Try to extract seconds from message like "Rate limit exceeded: 60 per 1 minute"
            parts = str(exc.detail).split()
            for i, part in enumerate(parts):
                if part.isdigit():
                    retry_after = int(part)
                    break
        except (ValueError, IndexError):
            pass

    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded. Please slow down.",
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


# Rate limit strings for use with @limiter.limit decorator
# Format: "X per Y" where Y is minute, hour, day, etc.
CHAT_RATE_LIMIT = f"{settings.rate_limit_requests_per_min} per minute"
