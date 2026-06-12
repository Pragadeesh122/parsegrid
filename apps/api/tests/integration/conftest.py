"""Integration fixtures: real Postgres, overridden DB dependency, no Celery/S3."""

import os
import time
from urllib.parse import urlparse

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.base import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://parsegrid:parsegrid@localhost:5436/parsegrid_test",
)


def _ensure_test_database() -> bool:
    """Create the test database if missing. False when Postgres is down."""
    import psycopg2

    parsed = urlparse(TEST_DATABASE_URL.replace("+asyncpg", ""))
    dbname = parsed.path.lstrip("/")
    try:
        conn = psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port,
            user=parsed.username,
            password=parsed.password,
            dbname="postgres",
            connect_timeout=3,
        )
    except Exception:
        return False
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{dbname}"')
    conn.close()
    return True


@pytest.fixture(scope="session")
def database_available():
    if not _ensure_test_database():
        pytest.skip("Postgres not reachable — integration tests skipped")


@pytest.fixture
async def client(database_available, monkeypatch):
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    # No real Celery, no real S3.
    from app.api.v1 import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "_dispatch_ocr", lambda job: None)
    monkeypatch.setattr(jobs_module, "delete_object_from_s3", lambda *a, **k: None)
    monkeypatch.setattr(jobs_module, "delete_prefix_from_s3", lambda *a, **k: 0)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def auth_header(user_id: str) -> dict[str, str]:
    token = pyjwt.encode(
        {"sub": user_id, "exp": int(time.time()) + 600},
        settings.auth_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}
