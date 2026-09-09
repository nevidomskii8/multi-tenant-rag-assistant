"""Rate limiting for denial-of-wallet defense (Phase 5, LLM10).

Uses slowapi with Redis backend for distributed rate limiting.
Per-user limits on /chat endpoint to prevent unbounded API cost.

Two limits are enforced:
1. RPM (requests per minute) — via slowapi decorator on /chat
2. TPH (tokens per hour) — via check_token_budget() called after LLM response
"""

import logging
from datetime import datetime, timezone

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings
from app.metrics import rate_limit_hits_total

log = logging.getLogger(__name__)


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
    # Extract endpoint for metrics (user_id excluded to prevent tenant leakage)
    endpoint = request.url.path

    # Record metric
    rate_limit_hits_total.labels(endpoint=endpoint).inc()

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


# --- Token budget (LLM10 — denial-of-wallet by token consumption) -------------


def _get_redis():
    """Get Redis client, or None if unavailable."""
    try:
        import redis

        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        return None


def record_token_usage(user_id: int, tokens: int) -> tuple[int, bool]:
    """Record tokens used and check if budget exceeded.

    Returns (current_total, exceeded). If Redis is unavailable, returns (0, False)
    and logs a warning — fail-open for token tracking (RPM is still enforced).
    """
    r = _get_redis()
    if r is None:
        log.warning("token_budget_redis_unavailable", user_id=user_id)
        return (0, False)

    # Key format: tokens:<user_id>:<hour> — expires after 1 hour
    hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"tokens:{user_id}:{hour_key}"

    try:
        current = r.incrby(key, tokens)
        if current == tokens:
            # First increment this hour — set TTL
            r.expire(key, 3600)

        exceeded = current > settings.rate_limit_tokens_per_hour
        if exceeded:
            log.warning(
                "token_budget_exceeded",
                user_id=user_id,
                current=current,
                limit=settings.rate_limit_tokens_per_hour,
            )
        return (current, exceeded)
    except Exception as e:
        log.warning("token_budget_error", user_id=user_id, error=str(e))
        return (0, False)


def get_token_usage(user_id: int) -> int:
    """Get current hourly token usage for a user. Returns 0 if unavailable."""
    r = _get_redis()
    if r is None:
        return 0

    hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"tokens:{user_id}:{hour_key}"

    try:
        val = r.get(key)
        return int(val) if val else 0
    except Exception:
        return 0
