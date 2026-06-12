"""Upgrade-path test: a legacy single-file job is backfilled into documents."""

import os
import subprocess
import uuid
from urllib.parse import urlparse

import psycopg2
import pytest

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://parsegrid:parsegrid@localhost:5436/parsegrid_test",
)
MIGRATION_DB = "parsegrid_migration_test"
PRE_DOCUMENTS_REVISION = "7a1c4e9b2d31"


def _admin_conn():
    parsed = urlparse(BASE_URL.replace("+asyncpg", ""))
    return psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname="postgres",
        connect_timeout=3,
    )


def _scratch_url() -> str:
    parsed = urlparse(BASE_URL.replace("+asyncpg", ""))
    return (
        f"postgresql+asyncpg://{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port}/{MIGRATION_DB}"
    )


def _alembic(env: dict, *args: str) -> None:
    result = subprocess.run(
        ["uv", "run", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, f"alembic {args} failed:\n{result.stderr}"


def test_upgrade_backfills_legacy_job(database_available):
    try:
        conn = _admin_conn()
    except Exception:
        pytest.skip("Postgres not reachable")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{MIGRATION_DB}"')
    conn.close()

    env = {**os.environ, "DATABASE_URL": _scratch_url()}

    # 1. Migrate to the last pre-documents revision and seed a legacy job.
    _alembic(env, "upgrade", PRE_DOCUMENTS_REVISION)

    parsed = urlparse(_scratch_url().replace("+asyncpg", ""))
    seed = psycopg2.connect(
        host=parsed.hostname,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=MIGRATION_DB,
    )
    job_id = str(uuid.uuid4())
    with seed.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs
                (id, user_id, filename, file_key, file_size, status, job_type,
                 output_format, progress, page_count, extracted_data,
                 created_at, updated_at)
            VALUES
                (%s, 'user-legacy', 'old.pdf', 'uploads/u/x/old.pdf', 123,
                 'COMPLETED', 'FULL', 'SQL', 100.0, 7, '{"t": []}',
                 now(), now())
            """,
            (job_id,),
        )
    seed.commit()

    # 2. Upgrade to head — the backfill must create exactly one document.
    _alembic(env, "upgrade", "head")

    with seed.cursor() as cur:
        cur.execute(
            "SELECT filename, file_key, file_size, page_count, status "
            "FROM documents WHERE job_id = %s",
            (job_id,),
        )
        rows = cur.fetchall()
    seed.close()

    assert len(rows) == 1
    filename, file_key, file_size, page_count, doc_status = rows[0]
    assert filename == "old.pdf"
    assert file_key == "uploads/u/x/old.pdf"
    assert file_size == 123
    assert page_count == 7
    assert doc_status == "EXTRACTED"  # extracted_data was non-null
