"""ParseGrid — Shared synchronous DB and Redis helpers for Celery workers.

Celery workers run synchronously. This module provides a singleton SQLAlchemy
engine (reused across all tasks in a worker process) and a cached Redis client
for PubSub status publishing. This prevents the anti-pattern of creating and
disposing an engine on every task invocation.
"""

import json
import logging
from functools import lru_cache

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_sync_url() -> str:
    """Convert the async database URL to a synchronous one for Celery workers."""
    url = settings.database_url
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "+psycopg2")
    if url.startswith("postgresql+psycopg2"):
        url = url.replace("postgresql+psycopg2", "postgresql")
    return url


@lru_cache(maxsize=1)
def get_sync_engine():
    """Singleton synchronous SQLAlchemy engine for Celery workers.

    pool_size=5 and max_overflow=10 prevent connection exhaustion
    while allowing reasonable concurrency within a single worker.
    """
    engine = create_engine(
        _build_sync_url(),
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    logger.info("Created shared sync engine for Celery worker")
    return engine


@lru_cache(maxsize=1)
def _get_redis_client() -> redis.Redis:
    """Singleton Redis client for PubSub publishing."""
    return redis.from_url(settings.redis_url)


def update_job(job_id: str, **fields) -> None:
    """Update job fields in PostgreSQL using the shared engine.

    Accepts arbitrary keyword arguments that map to column names on the jobs table.
    Automatically sets updated_at = NOW().
    """
    if not fields:
        return

    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    engine = get_sync_engine()

    with Session(engine) as session:
        session.execute(
            text(f"UPDATE jobs SET {set_clauses}, updated_at = NOW() WHERE id = :job_id"),
            {"job_id": job_id, **fields},
        )
        session.commit()


def get_job_field(job_id: str, *columns: str) -> dict:
    """Read specific columns from a job record.

    Returns a dict keyed by column name.
    """
    if not columns:
        raise ValueError("At least one column name is required")

    col_list = ", ".join(columns)
    engine = get_sync_engine()

    with Session(engine) as session:
        row = session.execute(
            text(f"SELECT {col_list} FROM jobs WHERE id = :job_id"),
            {"job_id": job_id},
        ).one()
        return dict(zip(columns, row))


def publish_status(job_id: str, status: str, progress: float, **extra) -> None:
    """Publish a status update to Redis PubSub for SSE streaming."""
    r = _get_redis_client()
    channel = f"job:{job_id}:status"
    data = {"status": status, "progress": progress, **extra}
    r.publish(channel, json.dumps(data))
