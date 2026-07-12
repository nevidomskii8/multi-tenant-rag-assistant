from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import settings
from app.rag import answer

app = FastAPI(title="multi-tenant-rag-assistant")


class ChatRequest(BaseModel):
    # Length cap = input validation / denial-of-wallet guard (LLM10).
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=4, ge=1, le=10)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    result = answer(req.question, k=req.k)
    # `context` (raw KB chunks) stays internal — the client gets answer + cited sources.
    return {"answer": result["answer"], "sources": result["sources"]}
