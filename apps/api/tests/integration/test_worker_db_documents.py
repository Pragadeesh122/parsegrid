"""Tier 3: sync worker helpers for the documents table (real Postgres)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.job import Document, DocumentStatus, Job, JobStatus
from app.worker import db as worker_db
from tests.integration.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded_job(database_available, monkeypatch):
    # Point the worker's sync engine at the test database.
    monkeypatch.setattr(worker_db.settings, "database_url", TEST_DATABASE_URL, raising=True)
    worker_db.get_sync_engine.cache_clear()

    engine = create_async_engine(TEST_DATABASE_URL)
    from sqlalchemy import text

    from app.models.base import Base

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    job_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            Job(
                id=job_id,
                user_id="u1",
                status=JobStatus.UPLOADED,
                progress=0.0,
                filename="a.pdf",
                file_key="uploads/u1/x/a.pdf",
                file_size=1,
            )
        )
        session.add(
            Document(
                id=doc_id,
                job_id=job_id,
                filename="a.pdf",
                file_key="uploads/u1/x/a.pdf",
                file_size=1,
                status=DocumentStatus.PENDING,
            )
        )
        await session.commit()

    yield job_id, doc_id

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    worker_db.get_sync_engine.cache_clear()


async def test_update_and_read_document(seeded_job):
    job_id, doc_id = seeded_job
    worker_db.update_document(doc_id, status="OCR_DONE", page_count=4)
    row = worker_db.get_document_field(doc_id, "status", "page_count", "job_id")
    assert row["status"] == "OCR_DONE"
    assert row["page_count"] == 4
    assert row["job_id"] == job_id


async def test_list_job_documents(seeded_job):
    job_id, _ = seeded_job
    docs = worker_db.list_job_documents(job_id, "id", "status", "filename")
    assert len(docs) == 1
    assert docs[0]["filename"] == "a.pdf"
