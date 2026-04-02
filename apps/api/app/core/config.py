"""ParseGrid API — Configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "ParseGrid API"
    fastapi_env: str = "development"
    debug: bool = True

    # --- Auth (shared with Next.js Auth.js) ---
    auth_secret: str = "change-me-in-production-min-32-characters"
    jwt_algorithm: str = "HS256"

    # --- Database (internal metadata) ---
    database_url: str = "postgresql+asyncpg://parsegrid:parsegrid@localhost:5432/parsegrid"

    # --- Redis (Celery broker + result backend) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- S3-Compatible Storage ---
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "parsegrid-uploads"
    s3_region: str = "us-east-1"

    # --- OpenAI ---
    openai_api_key: str = ""

    # --- LlamaParse ---
    llama_cloud_api_key: str = ""

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def is_production(self) -> bool:
        return self.fastapi_env == "production"


settings = Settings()
