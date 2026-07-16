from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    # Admin DSN — superuser `app`, used ONLY for migrations + operator ingest/seed.
    # It bypasses RLS (superuser + table owner), so it must never touch the request path.
    database_url: str = "postgresql://app:app@db:5432/app"
    # Runtime DSN — non-owner, non-superuser role `app_rt`. Every user request goes
    # through this so Row-Level Security is actually enforced (LLM08/LLM02).
    runtime_database_url: str = "postgresql://app_rt:app_rt@db:5432/app"

    # Local JWT issuer (Phase 2). Cognito replaces this in the AWS phase.
    # jwt_secret MUST be overridden in every real environment — the default is dev-only.
    jwt_secret: str = "dev-only-insecure-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # LLM inference. Haiku on dev — cheap, smaller denial-of-wallet surface (LLM10).
    # Switch to claude-opus-4-8 for prod/demo quality.
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-4-5"

    # Local embeddings (multilingual: en/ru/uk). Swapping the model is cheap:
    # change here + re-run ingestion; embedding_dim must match the DB schema.
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dim: int = 384


settings = Settings()
