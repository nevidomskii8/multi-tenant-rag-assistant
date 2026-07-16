"""Local identity: password hashing, JWT issue/verify, and user records.

Phase 2 auth is deliberately minimal — a local HS256 JWT issuer that stands in
for Cognito (arriving in the AWS phase). A token carries `user_id`, which the
request path turns into `SET LOCAL app.user_id` so Postgres RLS can scope every
query to the caller's rooms.

User records live in `users`, the one table intentionally left OUTSIDE RLS (auth
must look up an account by email before any identity exists). Because these are
identity-management operations — not room data — they run on the admin
connection; the room-data boundary is enforced entirely via `app_rt` + RLS.
"""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db import get_conn

_bearer = HTTPBearer(auto_error=True)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(user_id: int, email: str) -> str:
    now = datetime.now(tz=UTC)
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "iat": now,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> int:
    """FastAPI dependency → the authenticated user's id, or 401."""
    try:
        payload = jwt.decode(
            creds.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        ) from exc
    return int(payload["user_id"])


# --- User records (admin connection — identity management, not room data) -------


def create_user(email: str, password: str) -> int:
    """Insert a user, returning its id. Raises on duplicate email (UNIQUE)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
            (email, hash_password(password)),
        )
        user_id = cur.fetchone()[0]
        conn.commit()
    return user_id


def user_id_by_email(email: str) -> int | None:
    """Look up a user id by email (for adding members). None if absent."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
    return row[0] if row else None


def authenticate(email: str, password: str) -> int | None:
    """Return the user's id if the credentials are valid, else None."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, password_hash FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
    if row is None:
        return None
    user_id, password_hash = row
    if not verify_password(password, password_hash):
        return None
    return user_id
