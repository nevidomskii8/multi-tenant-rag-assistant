"""HTTP API for the multi-room RAG assistant.

Auth: a Bearer JWT (see app/auth). The token's user_id is turned into
`SET LOCAL app.user_id` on every request path (via session_for_user), so Postgres
RLS scopes all room data to the caller. Non-member rooms return **404, not 403**,
so a caller cannot distinguish a forbidden room from a non-existent one
(enumeration hardening — see ADR-004).
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import Response

from app import audit
from app import rooms as rooms_repo
from app.auth import (
    authenticate,
    create_access_token,
    create_user,
    get_current_user,
    user_id_by_email,
)
from app.config import settings
from app.db import session_for_user
from app.guardrails import scan_input
from app.ingest import ingest_text
from app.logging_config import configure_logging, get_logger
from app.middleware import CorrelationIdMiddleware, MetricsMiddleware
from app.rag import answer
from app.ratelimit import CHAT_RATE_LIMIT, limiter, rate_limit_exceeded_handler
from app.tracing import shutdown_tracing

# Initialize structured logging (Phase 5)
configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifespan: startup and shutdown hooks."""
    # Startup — nothing special needed
    yield
    # Shutdown — flush pending Langfuse traces
    log.info("shutdown_started")
    shutdown_tracing()
    log.info("shutdown_completed")


app = FastAPI(title="multi-tenant-rag-assistant", lifespan=lifespan)

# Rate limiting state (Phase 5 — LLM10 denial-of-wallet defense)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# Observability middleware (Phase 5)
app.add_middleware(MetricsMiddleware)
app.add_middleware(CorrelationIdMiddleware)


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class CreateRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AddMemberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class UploadDocRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    # Length cap = input validation / denial-of-wallet guard (LLM10).
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    room_id: int
    k: int = Field(default=4, ge=1, le=10)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus metrics endpoint (Phase 5)."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- Auth ----------------------------------------------------------------------


@app.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(req: Credentials) -> dict:
    try:
        user_id = create_user(req.email, req.password)
    except UniqueViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email already registered"
        ) from exc
    return {"user_id": user_id}


@app.post("/auth/login")
def login(req: Credentials) -> dict:
    user_id = authenticate(req.email, req.password)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    return {"token": create_access_token(user_id, req.email)}


# --- Rooms & membership --------------------------------------------------------


@app.post("/rooms", status_code=status.HTTP_201_CREATED)
def create_room(req: CreateRoomRequest, user_id: int = Depends(get_current_user)) -> dict:
    with session_for_user(user_id) as conn:
        room_id = rooms_repo.create_room(conn, req.name, user_id)
    return {"room_id": room_id, "name": req.name}


@app.post("/rooms/{room_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    room_id: int, req: AddMemberRequest, user_id: int = Depends(get_current_user)
) -> dict:
    target_id = user_id_by_email(req.email)
    if target_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    with session_for_user(user_id) as conn:
        owner = rooms_repo.room_owner(conn, room_id)
        if owner is None:  # room hidden from caller → indistinguishable from absent
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room not found")
        if owner != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="only the owner can add members"
            )
        try:
            rooms_repo.add_member(conn, room_id, target_id)
        except UniqueViolation:
            pass  # already a member → idempotent success
    return {"room_id": room_id, "user_id": target_id}


@app.post("/rooms/{room_id}/documents", status_code=status.HTTP_201_CREATED)
def upload_document(
    room_id: int, req: UploadDocRequest, user_id: int = Depends(get_current_user)
) -> dict:
    # The first "writer": a member loads data into their own room. Runs via
    # app_rt, so the write-side RLS (WITH CHECK) is what actually authorises it;
    # email/agent writers reuse this path in Phase C.
    with session_for_user(user_id) as conn:
        if not rooms_repo.is_member(conn, room_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room not found")
        n_chunks = ingest_text(conn, req.title, req.content, room_id)
    return {"room_id": room_id, "title": req.title, "chunks": n_chunks}


# --- Chat ----------------------------------------------------------------------


@app.post("/chat")
@limiter.limit(CHAT_RATE_LIMIT)
def chat(request: Request, req: ChatRequest, user_id: int = Depends(get_current_user)) -> dict:
    with session_for_user(user_id) as conn:
        if not rooms_repo.is_member(conn, req.room_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room not found")
    # Input guard (LLM01/LLM02): block injections, redact PII from the question
    # before it reaches retrieval or the model. Fail-closed — a down sidecar denies.
    question_to_use = req.question
    if settings.guardrails_enabled:
        guard = scan_input(req.question)
        with session_for_user(user_id) as conn:
            audit.record_scan(
                conn, room_id=req.room_id, user_id=user_id, event_type=audit.INPUT, result=guard
            )
        if not guard.allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="request blocked by guardrails"
            )
        # Use the sanitized version (PII masked) for retrieval and LLM
        question_to_use = guard.sanitized

    result = answer(question_to_use, room_id=req.room_id, user_id=user_id, k=req.k)

    log.info(
        "chat_completed",
        user_id=user_id,
        room_id=req.room_id,
        question_len=len(req.question),
        sources_count=len(result["sources"]),
        guardrail_verdict=result.get("guardrail", {}).get("verdict"),
    )

    # `context` (raw KB chunks) stays internal — the client gets answer + cited sources.
    return {"answer": result["answer"], "sources": result["sources"]}
