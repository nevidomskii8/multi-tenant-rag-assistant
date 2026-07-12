from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    database_url: str = "postgresql://app:app@db:5432/app"

    # LLM inference. Haiku on dev — cheap, smaller denial-of-wallet surface (LLM10).
    # Switch to claude-opus-4-8 for prod/demo quality.
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-4-5"

    # Local embeddings (multilingual: en/ru/uk). Swapping the model is cheap:
    # change here + re-run ingestion; embedding_dim must match the DB schema.
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dim: int = 384


settings = Settings()
