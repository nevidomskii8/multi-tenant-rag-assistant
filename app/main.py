from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="multi-tenant-rag-assistant")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
