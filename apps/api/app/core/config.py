"""ParseGrid API — Configuration via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "ParseGrid API"
    fastapi_env: str = "development"
    debug: bool = True

    # --- Auth (shared with Next.js Auth.js) ---
    auth_secret: str = "parsegrid-dev-secret-minimum-32-characters-long"
    jwt_algorithm: str = "HS256"

    # --- Database (internal metadata) ---
    database_url: str = "postgresql+asyncpg://parsegrid:parsegrid@localhost:5436/parsegrid"

    # --- Redis (Celery broker + result backend) ---
    redis_url: str = "redis://localhost:6380/0"

    # --- S3-Compatible Storage ---
    s3_endpoint_url: str | None = "http://localhost:9002"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "parsegrid-uploads"
    s3_region: str = "us-east-1"

    # --- OpenAI ---
    openai_api_key: str = ""

    # --- Neo4j (GRAPH output provider) ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "parsegrid"
    neo4j_database: str = "neo4j"

    # --- Qdrant (VECTOR output provider) ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None

    # --- LlamaParse ---
    llama_cloud_api_key: str = ""

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def is_production(self) -> bool:
        return self.fastapi_env == "production"


settings = Settings()
