# Dataset Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a Job into a dataset: created from 1..N files, extendable after completion with compatibility-checked appends, always provisioned by rebuilding the output DB from per-document extraction buckets.

**Architecture:** New `documents` child table under `Job` (per-file state + extraction buckets); pipeline becomes per-document (OCR group → unified multi-doc profiling → per-doc extraction fan-out → per-doc buckets); reconciliation/provisioning becomes a job-level `rebuild_dataset` task that unions all documents' buckets and drop-recreates the output schema. Appends run extraction against the locked model, compute a compatibility report, and either auto-rebuild or pause for force/cancel.

**Tech Stack:** FastAPI + async SQLAlchemy 2.0 + Alembic, Celery (group/chord), Next.js 16 + TanStack Query, pytest (Tier 1/2/3 conventions from the Foundation suite).

**Spec:** `docs/superpowers/specs/2026-06-11-dataset-consolidation-design.md`

## Conventions (read first)

- Working directory for all backend commands: `apps/api/`. Run tests with `uv run pytest`. Quality gates per task: `uv run ruff check <files>` and `uv run ruff format <files>` (then `--check`) before committing; full suite `uv run pytest -q` must be green at the end of every task.
- Commit directly on `main` (no branches/worktrees). End every commit message with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Never stage the untracked `scripts/` directory.
- The local Postgres container must be up for integration tests: `docker compose -f infrastructure/docker-compose.yml up -d postgres` (service name `postgres`, mapped 5436:5432).
- Tests of new behavior follow TDD (write test → expect FAIL → implement → PASS). Worker-task tests are Tier 2: monkeypatch `app.worker.db` helpers and S3/Celery; never call OpenAI or real Redis.
- **Pipeline continuity note:** between Tasks 7 and 10 the live pipeline is intentionally mid-migration (OCR writes per-document S3 paths before extraction reads them). The test suite stays green at every commit; just don't run real end-to-end jobs until Task 10 lands.
- Line length is 100 (ruff). Apply `ruff format` output verbatim when it reflows plan code.

---

### Task 1: Document model, JobStatus additions, migration with backfill

**Files:**
- Modify: `apps/api/app/models/job.py`
- Create: `apps/api/alembic/versions/a1d0c5e7b201_add_documents_table.py`

- [ ] **Step 1: Add DocumentStatus + Document to the models, extend JobStatus**

In `apps/api/app/models/job.py`:

Add to `JobStatus` (after `PROVISIONING = "PROVISIONING"`):

```python
    APPENDING = "APPENDING"
    AWAITING_APPEND_REVIEW = "AWAITING_APPEND_REVIEW"
```

Add after the `OutputFormat` class:

```python
class DocumentStatus(str, enum.Enum):  # noqa: UP042 -- str mixin needed for str(status) == "VALUE"
    """Per-file lifecycle inside a dataset Job."""

    PENDING = "PENDING"
    OCR_PROCESSING = "OCR_PROCESSING"
    OCR_DONE = "OCR_DONE"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
```

Add to the `Job` class, next to the existing `chunks` relationship:

```python
    documents: Mapped[list["Document"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Document.created_at",
        lazy="selectin",
    )
```

(`lazy="selectin"` so async endpoints and Pydantic `from_attributes` never trip lazy IO.)

Add after the `Job` class (before `DocumentChunk`):

```python
class Document(Base, TimestampMixin):
    """One uploaded file inside a dataset Job (Dataset Consolidation)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", create_constraint=True),
        nullable=False,
        default=DocumentStatus.PENDING,
    )
    extracted_buckets: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="This file's pre-reconciliation output: "
        '{"tables": {tbl: {"rows": [...], "chunk_pages": {chunk_key: [pages]}}}}',
    )
    compat_report: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Append compatibility stats; null for founding documents.",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="documents")

    def __repr__(self) -> str:
        return f"<Document(id={self.id!r}, job_id={self.job_id!r}, status={self.status!r})>"
```

Do **not** remove `filename`/`file_key`/`file_size`/`page_count` from `Job` yet — Task 12 drops them after the pipeline and API stop reading them. (Dual-write period keeps every intermediate commit green.)

- [ ] **Step 2: Write the migration**

Create `apps/api/alembic/versions/a1d0c5e7b201_add_documents_table.py`. First check the current head: run `cd apps/api && uv run alembic heads` — expected `7a1c4e9b2d31`. If different, use the actual head as `down_revision`.

```python
"""add documents table, backfill from jobs, extend job_status enum

Revision ID: a1d0c5e7b201
Revises: 7a1c4e9b2d31
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d0c5e7b201"
down_revision: str | None = "7a1c4e9b2d31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New job lifecycle states (PG12+ allows ADD VALUE in a transaction as
    # long as the new value is not used in the same transaction).
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'APPENDING'")
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'AWAITING_APPEND_REVIEW'")

    document_status = sa.Enum(
        "PENDING",
        "OCR_PROCESSING",
        "OCR_DONE",
        "EXTRACTING",
        "EXTRACTED",
        "FAILED",
        "REJECTED",
        name="document_status",
    )
    document_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("file_key", sa.String(length=1024), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(name="document_status", create_type=False),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("extracted_buckets", sa.JSON(), nullable=True),
        sa.Column("compat_report", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_documents_job_id", "documents", ["job_id"])

    # Backfill: every existing job becomes a single-document dataset.
    # Status mapping per spec: FAILED job -> FAILED; extracted_data present ->
    # EXTRACTED; page_count set -> OCR_DONE; else PENDING.
    op.execute(
        """
        INSERT INTO documents
            (id, job_id, filename, file_key, file_size, page_count, status,
             created_at, updated_at)
        SELECT
            gen_random_uuid()::text,
            j.id,
            j.filename,
            j.file_key,
            j.file_size,
            j.page_count,
            (CASE
                WHEN j.status = 'FAILED' THEN 'FAILED'
                WHEN j.extracted_data IS NOT NULL THEN 'EXTRACTED'
                WHEN j.page_count IS NOT NULL THEN 'OCR_DONE'
                ELSE 'PENDING'
            END)::document_status,
            j.created_at,
            j.updated_at
        FROM jobs j
        """
    )


def downgrade() -> None:
    op.drop_index("ix_documents_job_id", table_name="documents")
    op.drop_table("documents")
    sa.Enum(name="document_status").drop(op.get_bind(), checkfirst=True)
    # job_status enum values are left in place (removing enum values requires
    # recreating the type — same precedent as the dead SCHEMA_* members).
```

- [ ] **Step 3: Apply and verify**

Run: `cd apps/api && uv run alembic upgrade head`
Expected: migration applies cleanly.
Run: `uv run pytest -q`
Expected: full suite green (106 passed, or 100 + 6 skipped without Postgres). The integration conftest creates the `documents` table automatically via `Base.metadata`.

- [ ] **Step 4: Lint + commit**

```bash
uv run ruff check app/models/job.py alembic/versions/a1d0c5e7b201_add_documents_table.py
uv run ruff format app/models/job.py alembic/versions/a1d0c5e7b201_add_documents_table.py
git add app/models/job.py alembic/versions/a1d0c5e7b201_add_documents_table.py
git commit -m "feat: add documents table with backfill; APPENDING job states"
```

(`alembic/versions` is ruff-excluded; the check on it is a no-op — harmless.)

---

### Task 2: Migration upgrade test

**Files:**
- Test: `apps/api/tests/integration/test_migration.py`

- [ ] **Step 1: Confirm how alembic gets its URL**

Read `apps/api/alembic/env.py` and confirm it derives the database URL from `app.core.config.settings.database_url` (or the `DATABASE_URL` env var). The test below passes `DATABASE_URL` through the subprocess environment; if env.py hardcodes something else, adapt the env-var name accordingly and report the difference.

- [ ] **Step 2: Write the test**

```python
# apps/api/tests/integration/test_migration.py
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
```

- [ ] **Step 3: Run — expect PASS**

Run: `uv run pytest tests/integration/test_migration.py -v`
Expected: 1 PASS (or skip without Postgres). The test reuses the existing `database_available` session fixture from `tests/integration/conftest.py`.

NOTE: this test runs `alembic upgrade` from the *current* head; when Task 12 adds the column-drop migration, the same test transparently covers it (the legacy insert happens at `PRE_DOCUMENTS_REVISION`, before the columns vanish).

- [ ] **Step 4: Full suite, lint, commit**

```bash
uv run pytest -q
uv run ruff check tests/integration/test_migration.py && uv run ruff format tests/integration/test_migration.py
git add tests/integration/test_migration.py
git commit -m "test: migration backfills legacy jobs into documents"
```

---

### Task 3: Worker DB document helpers

**Files:**
- Modify: `apps/api/app/worker/db.py`
- Test: `apps/api/tests/integration/test_worker_db_documents.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/integration/test_worker_db_documents.py
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
    monkeypatch.setattr(
        worker_db.settings, "database_url", TEST_DATABASE_URL, raising=True
    )
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
            Job(id=job_id, user_id="u1", status=JobStatus.UPLOADED, progress=0.0,
                filename="a.pdf", file_key="uploads/u1/x/a.pdf", file_size=1)
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
```

NOTE: `Job` still has `filename`/`file_key`/`file_size` until Task 12; the seed sets them. **Task 12 must update this seed** (remove those three kwargs) — that is called out there.

Run: `uv run pytest tests/integration/test_worker_db_documents.py -v`
Expected: FAIL — `AttributeError: module 'app.worker.db' has no attribute 'update_document'`.

- [ ] **Step 2: Implement the helpers**

Append to `apps/api/app/worker/db.py`:

```python
def update_document(document_id: str, **fields) -> None:
    """Update document fields by id (mirrors update_job)."""
    if not fields:
        return
    set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
    engine = get_sync_engine()
    with Session(engine) as session:
        session.execute(
            text(
                f"UPDATE documents SET {set_clauses}, updated_at = NOW() "
                "WHERE id = :document_id"
            ),
            {"document_id": document_id, **fields},
        )
        session.commit()


def get_document_field(document_id: str, *columns: str) -> dict:
    """Read specific columns from a document record."""
    if not columns:
        raise ValueError("At least one column name is required")
    col_list = ", ".join(columns)
    engine = get_sync_engine()
    with Session(engine) as session:
        row = session.execute(
            text(f"SELECT {col_list} FROM documents WHERE id = :document_id"),
            {"document_id": document_id},
        ).one()
        return dict(zip(columns, row))


def list_job_documents(job_id: str, *columns: str) -> list[dict]:
    """Read specific columns for every document of a job, oldest first."""
    if not columns:
        raise ValueError("At least one column name is required")
    col_list = ", ".join(columns)
    engine = get_sync_engine()
    with Session(engine) as session:
        rows = session.execute(
            text(
                f"SELECT {col_list} FROM documents WHERE job_id = :job_id "
                "ORDER BY created_at"
            ),
            {"job_id": job_id},
        ).all()
        return [dict(zip(columns, row)) for row in rows]
```

- [ ] **Step 3: Run — expect PASS, full suite, commit**

```bash
uv run pytest tests/integration/test_worker_db_documents.py -v   # 2 PASS
uv run pytest -q                                                  # all green
uv run ruff check app/worker/db.py tests/integration/test_worker_db_documents.py
uv run ruff format app/worker/db.py tests/integration/test_worker_db_documents.py
git add app/worker/db.py tests/integration/test_worker_db_documents.py
git commit -m "feat: sync worker helpers for documents table"
```

---

### Task 4: Consolidation service (budget split, bucket union, compat report)

**Files:**
- Create: `apps/api/app/services/consolidation.py`
- Test: `apps/api/tests/test_consolidation.py`

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_consolidation.py
from app.services.consolidation import (
    allocate_sampling_budget,
    build_compat_report,
    union_buckets,
)
from tests.factories import make_column, make_model, make_table

INVOICES = make_table(
    "invoices", [make_column("invoice_number", pk=True), make_column("note")]
)
LINES = make_table("lines", [make_column("description")])  # no PK
MODEL = make_model([INVOICES, LINES])


class TestAllocateSamplingBudget:
    def test_empty(self):
        assert allocate_sampling_budget({}) == {}

    def test_single_doc_gets_full_budget_capped_by_pages(self):
        assert allocate_sampling_budget({"a": 8}, budget=15) == {"a": 8}
        assert allocate_sampling_budget({"a": 40}, budget=15) == {"a": 15}

    def test_floor_of_three_for_small_docs(self):
        alloc = allocate_sampling_budget({"a": 2, "b": 100}, budget=15)
        assert alloc["a"] == 2  # floor capped by the doc's own page count
        assert alloc["b"] == 13
        assert sum(alloc.values()) == 15

    def test_proportional_split_is_deterministic(self):
        counts = {"a": 50, "b": 50, "c": 10}
        first = allocate_sampling_budget(counts, budget=15)
        assert first == allocate_sampling_budget(counts, budget=15)
        assert sum(first.values()) == 15
        assert all(first[d] >= 3 for d in counts)
        assert first["a"] >= first["c"]

    def test_floors_win_when_budget_too_small(self):
        # 6 docs x floor 3 = 18 > 15: floors are kept, budget overshoots.
        alloc = allocate_sampling_budget({f"d{i}": 30 for i in range(6)}, budget=15)
        assert all(v == 3 for v in alloc.values())


class TestUnionBuckets:
    def test_unions_rows_and_pages_with_string_keys(self):
        doc_a = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "1", "__chunk_index": "docA:0"}],
                    "chunk_pages": {"docA:0": [1]},
                }
            }
        }
        doc_b = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "2", "__chunk_index": "docB:0"}],
                    "chunk_pages": {"docB:0": [4]},
                },
                "lines": {
                    "rows": [{"description": "x", "__chunk_index": "docB:1"}],
                    "chunk_pages": {"docB:1": [5]},
                },
            }
        }
        rows, pages = union_buckets([("docA", doc_a), ("docB", doc_b)])
        assert len(rows["invoices"]) == 2
        assert rows["lines"][0]["description"] == "x"
        assert pages["invoices"] == {"docA:0": [1], "docB:0": [4]}

    def test_legacy_integer_keys_are_normalized_to_strings(self):
        # Pre-consolidation merged_buckets.json used bare int chunk indexes.
        legacy = {
            "tables": {
                "invoices": {
                    "rows": [{"invoice_number": "9", "__chunk_index": 0}],
                    "chunk_pages": {"0": [2]},
                }
            }
        }
        rows, pages = union_buckets([("docL", legacy)])
        assert rows["invoices"][0]["__chunk_index"] == "0"
        assert pages["invoices"] == {"0": [2]}


class TestBuildCompatReport:
    def test_healthy_append_auto_continues(self):
        buckets = {
            "invoices": [{"invoice_number": "1"}, {"invoice_number": "2"}],
            "lines": [{"description": "a"}],
        }
        report = build_compat_report(buckets, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is False
        assert report["total_rows"] == 3
        assert report["rows_per_table"] == {"invoices": 2, "lines": 1}

    def test_zero_rows_needs_review(self):
        report = build_compat_report({}, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is True
        assert any("no rows" in r for r in report["reasons"])

    def test_pk_null_ratio_over_threshold_needs_review(self):
        buckets = {
            "invoices": [
                {"invoice_number": None},
                {"invoice_number": None},
                {"invoice_number": "ok"},
            ]
        }
        report = build_compat_report(buckets, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is True
        assert report["pk_null_rows"] == 2

    def test_all_pk_tables_empty_needs_review(self):
        # Rows only in the PK-less table.
        buckets = {"lines": [{"description": "a"}]}
        report = build_compat_report(buckets, MODEL, max_pk_null_ratio=0.5)
        assert report["needs_review"] is True
        assert report["empty_pk_tables"] == ["invoices"]

    def test_histogram_drift_is_flag_only(self):
        buckets = {"invoices": [{"invoice_number": "1"}]}
        report = build_compat_report(
            buckets,
            MODEL,
            max_pk_null_ratio=0.5,
            dataset_histogram={"table": 90, "text": 10},
            document_histogram={"text": 100},
        )
        assert report["needs_review"] is False  # drift alone never blocks
        assert report["profile_drift"] is not None
```

Run: `uv run pytest tests/test_consolidation.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.consolidation`.

- [ ] **Step 2: Implement the module**

```python
# apps/api/app/services/consolidation.py
"""ParseGrid — Dataset Consolidation pure logic.

Three deterministic pieces, no IO, no LLM:
- allocate_sampling_budget: split the profiling page budget across documents
- union_buckets: merge per-document extraction buckets for reconciliation
- build_compat_report: measure how well an appended file fits the locked model
"""

from __future__ import annotations

from typing import Any

from app.schemas.extraction_model import DatabaseModel

SAMPLING_FLOOR = 3


def allocate_sampling_budget(
    page_counts: dict[str, int], budget: int = 15, floor: int = SAMPLING_FLOOR
) -> dict[str, int]:
    """Split `budget` sampled pages across documents, proportional to size.

    Every document gets min(floor, its page count). Remaining budget goes one
    page at a time to the document with the most unsampled pages (ties broken
    by document id), so the result is deterministic. When floors alone exceed
    the budget, floors win — small documents always keep their anchors.
    """
    if not page_counts:
        return {}
    alloc = {doc: min(floor, max(count, 0)) for doc, count in page_counts.items()}
    remaining = budget - sum(alloc.values())
    while remaining > 0:
        candidates = [d for d in page_counts if alloc[d] < page_counts[d]]
        if not candidates:
            break
        best = sorted(candidates, key=lambda d: (-(page_counts[d] - alloc[d]), d))[0]
        alloc[best] += 1
        remaining -= 1
    return alloc


def union_buckets(
    doc_buckets: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, list[dict]], dict[str, dict[str, list[int]]]]:
    """Merge per-document bucket payloads into reconcile_model inputs.

    Each payload has the shape
    `{"tables": {tbl: {"rows": [...], "chunk_pages": {chunk_key: [pages]}}}}`.
    Chunk keys and row `__chunk_index` markers are normalized to strings so
    legacy (integer-keyed) buckets and document-prefixed keys coexist.
    """
    bucketed_rows: dict[str, list[dict]] = {}
    chunk_pages: dict[str, dict[str, list[int]]] = {}
    for _document_id, payload in doc_buckets:
        tables = (payload or {}).get("tables") or {}
        for table_name, bucket in tables.items():
            rows = bucket.get("rows") or []
            for row in rows:
                row = dict(row)
                if "__chunk_index" in row:
                    row["__chunk_index"] = str(row["__chunk_index"])
                bucketed_rows.setdefault(table_name, []).append(row)
            pages = bucket.get("chunk_pages") or {}
            chunk_pages.setdefault(table_name, {}).update(
                {str(k): list(v) for k, v in pages.items()}
            )
    return bucketed_rows, chunk_pages


def build_compat_report(
    buckets: dict[str, list[dict]],
    locked_model: DatabaseModel,
    *,
    max_pk_null_ratio: float,
    dataset_histogram: dict[str, int] | None = None,
    document_histogram: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Measure how well an appended document's extraction fits the model.

    Pull-in rule (any one triggers needs_review):
    - zero rows extracted in total
    - more than `max_pk_null_ratio` of rows missing a primary-key component
    - every PK-bearing table received zero rows
    Histogram drift between the document and the dataset profile is recorded
    but never blocks on its own.
    """
    pk_columns = {
        t.table_name: [c.name for c in t.columns if c.is_primary_key]
        for t in locked_model.tables
    }
    pk_tables = sorted(t for t, cols in pk_columns.items() if cols)

    rows_per_table = {t: len(rows) for t, rows in buckets.items() if rows}
    total_rows = sum(rows_per_table.values())

    pk_null_rows = 0
    for table_name, rows in buckets.items():
        cols = pk_columns.get(table_name) or []
        if not cols:
            continue
        for row in rows:
            if any(row.get(c) in (None, "") for c in cols):
                pk_null_rows += 1
    pk_null_ratio = (pk_null_rows / total_rows) if total_rows else 0.0

    empty_pk_tables = [t for t in pk_tables if not rows_per_table.get(t)]

    reasons: list[str] = []
    if total_rows == 0:
        reasons.append("no rows extracted from this document")
    if total_rows and pk_null_ratio > max_pk_null_ratio:
        reasons.append(
            f"{pk_null_ratio:.0%} of rows are missing primary-key components "
            f"(threshold {max_pk_null_ratio:.0%})"
        )
    if total_rows and pk_tables and len(empty_pk_tables) == len(pk_tables):
        reasons.append("no primary-key table received any rows")

    profile_drift = None
    if dataset_histogram and document_histogram:
        profile_drift = {
            "dataset": dataset_histogram,
            "document": document_histogram,
        }

    return {
        "total_rows": total_rows,
        "rows_per_table": rows_per_table,
        "pk_null_rows": pk_null_rows,
        "pk_null_ratio": round(pk_null_ratio, 4),
        "pk_tables": pk_tables,
        "empty_pk_tables": empty_pk_tables,
        "profile_drift": profile_drift,
        "reasons": reasons,
        "needs_review": bool(reasons),
    }
```

- [ ] **Step 3: Run — expect 11 PASS, full suite, commit**

```bash
uv run pytest tests/test_consolidation.py -v
uv run pytest -q
uv run ruff check app/services/consolidation.py tests/test_consolidation.py
uv run ruff format app/services/consolidation.py tests/test_consolidation.py
git add app/services/consolidation.py tests/test_consolidation.py
git commit -m "feat: consolidation logic — budget split, bucket union, compat report"
```

---

### Task 5: Profiling budget parameter

**Files:**
- Modify: `apps/api/app/services/profiling.py`
- Test: append to `apps/api/tests/test_profiling.py`

- [ ] **Step 1: Write the failing tests** (append to the existing file; `_page`/`_doc` helpers already exist there)

```python
def test_budget_overrides_default_cap():
    doc = _doc([_page(n, ["text"]) for n in range(1, 51)])
    sampled, _ = profile_document(doc, budget=6)
    assert len(sampled) == 6
    assert sampled == sorted(sampled)
    assert 1 in sampled  # front anchor survives trimming


def test_budget_short_doc_still_takes_everything_under_budget():
    doc = _doc([_page(n, ["text"]) for n in range(1, 5)])
    sampled, _ = profile_document(doc, budget=6)
    assert sampled == [1, 2, 3, 4]


def test_default_budget_unchanged():
    doc = _doc([_page(n, ["text"]) for n in range(1, 51)])
    assert profile_document(doc) == profile_document(doc, budget=MAX_SAMPLED_PAGES)
```

(The first test also needs `MAX_SAMPLED_PAGES` in the existing import line at the top of `tests/test_profiling.py` — it is already imported there.)

Run: `uv run pytest tests/test_profiling.py -v`
Expected: the three new tests FAIL with `TypeError: profile_document() got an unexpected keyword argument 'budget'`; the existing 7 still PASS.

- [ ] **Step 2: Implement**

In `apps/api/app/services/profiling.py`, change the signature and replace every use of `MAX_SAMPLED_PAGES` inside `profile_document` with a local `cap`:

```python
def profile_document(
    ocr_json: dict[str, Any],
    budget: int | None = None,
) -> tuple[list[int], dict[str, int]]:
```

At the top of the function body (after the `total_pages == 0` early return):

```python
    cap = budget if budget is not None else MAX_SAMPLED_PAGES
```

Then replace, inside this function only:
- `if len(selected) >= MAX_SAMPLED_PAGES:` (diversity loop) → `if len(selected) >= cap:`
- `if len(selected) < MAX_SAMPLED_PAGES and total_pages > MAX_SAMPLED_PAGES:` → `if len(selected) < cap and total_pages > cap:`
- `remaining_slots = MAX_SAMPLED_PAGES - len(selected)` → `remaining_slots = cap - len(selected)`
- the inner filler-loop `if len(selected) >= MAX_SAMPLED_PAGES:` → `if len(selected) >= cap:`
- `if total_pages <= MAX_SAMPLED_PAGES:` → `if total_pages <= cap:`

Finally, because anchors (front 3 + back 2 + diversity) can exceed a small budget, add a deterministic trim just before the return:

```python
    sampled = sorted(p for p in selected if 1 <= p <= total_pages)
    if len(sampled) > cap:
        # Keep the earliest pages first — deterministic and preserves the
        # front-matter anchors that matter most for model discovery.
        sampled = sampled[:cap]
    return sampled, dict(histogram)
```

Also update the docstring's Args section to mention `budget: optional override of MAX_SAMPLED_PAGES (used for multi-document budget splitting)`.

- [ ] **Step 3: Run — all profiling tests PASS, full suite, commit**

```bash
uv run pytest tests/test_profiling.py -v   # 10 PASS
uv run pytest -q
uv run ruff check app/services/profiling.py tests/test_profiling.py
uv run ruff format app/services/profiling.py tests/test_profiling.py
git add app/services/profiling.py tests/test_profiling.py
git commit -m "feat: profile_document accepts a per-document sampling budget"
```

---

### Task 6: API create accepts multiple files; responses expose documents

**Files:**
- Modify: `apps/api/app/schemas/job.py`
- Modify: `apps/api/app/api/v1/jobs.py` (create_job, delete_job)
- Test: append to `apps/api/tests/integration/test_jobs_api.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/integration/test_jobs_api.py`)

```python
MULTI_PAYLOAD = {
    "files": [
        {"filename": "jan.pdf", "file_key": "uploads/u/x/jan.pdf", "file_size": 100},
        {"filename": "feb.pdf", "file_key": "uploads/u/y/feb.pdf", "file_size": 200},
    ]
}


async def test_create_multi_file_lists_documents(client):
    res = await client.post("/api/v1/jobs", json=MULTI_PAYLOAD, headers=auth_header("user-a"))
    assert res.status_code == 201
    job = res.json()
    assert job["document_count"] == 2
    assert [d["filename"] for d in job["documents"]] == ["jan.pdf", "feb.pdf"]
    assert all(d["status"] == "PENDING" for d in job["documents"])


async def test_legacy_single_file_body_still_works(client):
    job = await _create(client, "user-a")
    assert job["document_count"] == 1
    assert job["documents"][0]["file_key"] == PAYLOAD["file_key"]


async def test_targeted_rejects_multiple_files(client):
    res = await client.post(
        "/api/v1/jobs",
        json={**MULTI_PAYLOAD, "job_type": "TARGETED"},
        headers=auth_header("user-a"),
    )
    assert res.status_code == 400
```

Run: `uv run pytest tests/integration/test_jobs_api.py -v`
Expected: the 3 new tests FAIL (422 on `files`, KeyError `document_count`); the 6 existing tests PASS.

- [ ] **Step 2: Update schemas**

In `apps/api/app/schemas/job.py`:

Change the imports line to include the new pieces:

```python
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.job import DocumentStatus, JobStatus, JobType, OutputFormat
```

Replace `JobCreateRequest` with:

```python
class JobFileSpec(BaseModel):
    """One uploaded file inside a job-creation request."""

    filename: str = Field(..., min_length=1, max_length=512)
    file_key: str = Field(..., min_length=1, max_length=1024, description="S3 object key")
    file_size: int = Field(..., gt=0)


class JobCreateRequest(BaseModel):
    """Request body for creating a new extraction job.

    Accepts either the multi-file `files` list or the legacy single-file
    triple (filename/file_key/file_size); the legacy shape is normalized
    into a one-element `files` list.
    """

    files: list[JobFileSpec] | None = Field(default=None, min_length=1, max_length=50)
    filename: str | None = Field(default=None, min_length=1, max_length=512)
    file_key: str | None = Field(default=None, min_length=1, max_length=1024)
    file_size: int | None = Field(default=None, gt=0)
    output_format: OutputFormat = OutputFormat.SQL
    job_type: JobType = JobType.FULL

    @model_validator(mode="after")
    def _normalize_files(self) -> "JobCreateRequest":
        if self.files is None:
            if not (self.filename and self.file_key and self.file_size):
                raise ValueError(
                    "provide either files[] or filename/file_key/file_size"
                )
            self.files = [
                JobFileSpec(
                    filename=self.filename,
                    file_key=self.file_key,
                    file_size=self.file_size,
                )
            ]
        return self
```

Add after `TargetQueryRequest`:

```python
class DocumentResponse(BaseModel):
    """Per-file state inside a dataset job."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    filename: str
    file_key: str
    file_size: int
    page_count: int | None = None
    status: DocumentStatus
    compat_report: dict | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
```

In `JobResponse`, add (keep the legacy `filename`/`file_key`/`file_size`/`page_count` fields for now — Task 12 removes them):

```python
    documents: list[DocumentResponse] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def document_count(self) -> int:
        return len(self.documents)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pages(self) -> int | None:
        pages = [d.page_count for d in self.documents if d.page_count]
        return sum(pages) if pages else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_file_size(self) -> int:
        return sum(d.file_size for d in self.documents)
```

- [ ] **Step 3: Update the endpoints**

In `apps/api/app/api/v1/jobs.py`:

Add `Document` to the models import: `from app.models.job import Document, Job, JobStatus, JobType`.

Replace the body of `create_job` with:

```python
    if body.job_type == JobType.TARGETED and len(body.files) > 1:
        raise HTTPException(
            status_code=400,
            detail="TARGETED jobs accept exactly one file",
        )

    first = body.files[0]
    job = Job(
        id=str(uuid.uuid4()),
        user_id=user.sub,
        # Legacy columns (dropped in the column-removal migration); kept in
        # sync with documents[0] during the transition.
        filename=first.filename,
        file_key=first.file_key,
        file_size=first.file_size,
        output_format=body.output_format,
        job_type=body.job_type,
        status=JobStatus.UPLOADED,
        progress=0.0,
    )
    db.add(job)
    for spec in body.files:
        db.add(
            Document(
                id=str(uuid.uuid4()),
                job_id=job.id,
                filename=spec.filename,
                file_key=spec.file_key,
                file_size=spec.file_size,
            )
        )
    await db.commit()
    await db.refresh(job)

    # Enqueue OCR processing task via Celery
    from app.worker.tasks.ocr import process_document

    process_document.apply_async(args=[job.id])

    return job
```

(The dispatch is still the legacy single-job call; Task 7 replaces it with the per-document chord.)

In `delete_job`, replace the upload-prefix block with a loop over documents (job.documents is selectin-loaded):

```python
    for document in job.documents:
        upload_prefix = (
            f"{document.file_key.rsplit('/', 1)[0]}/" if "/" in document.file_key else None
        )
        if upload_prefix:
            delete_prefix_from_s3(upload_prefix)
        else:
            delete_object_from_s3(document.file_key)
    delete_prefix_from_s3(f"parsed/{job_id}/")
    delete_prefix_from_s3(f"extracted/{job_id}/")
```

- [ ] **Step 4: Run — expect PASS, full suite, commit**

```bash
uv run pytest tests/integration/test_jobs_api.py -v   # 9 PASS
uv run pytest -q
uv run ruff check app/schemas/job.py app/api/v1/jobs.py tests/integration/test_jobs_api.py
uv run ruff format app/schemas/job.py app/api/v1/jobs.py tests/integration/test_jobs_api.py
git add app/schemas/job.py app/api/v1/jobs.py tests/integration/test_jobs_api.py
git commit -m "feat: multi-file job creation; documents in job responses"
```

---

### Task 7: Per-document OCR with creation chord

**Files:**
- Modify: `apps/api/app/worker/tasks/ocr.py`
- Modify: `apps/api/app/worker/tasks/rag.py` (per-document S3 path)
- Modify: `apps/api/app/worker/callbacks.py` (task map + document failure)
- Modify: `apps/api/app/api/v1/jobs.py` (create_job + reject_model dispatch)
- Modify: `apps/api/tests/integration/conftest.py` (patch the new dispatch helper)
- Test: `apps/api/tests/test_ocr_task.py`

- [ ] **Step 1: Rewrite `process_document` to be per-document**

Replace the task function in `apps/api/app/worker/tasks/ocr.py` (keep the module docstring, imports, and decorator pattern; add `from app.worker.db import ..., update_document` and `from celery import chord, group` is NOT needed here):

```python
@celery_app.task(
    name="app.worker.tasks.ocr.process_document",
    bind=True,
    max_retries=3,
    queue="ocr",
)
def process_document(self, job_id: str, document_id: str, append: bool = False):
    """OCR one document of a job.

    1. Download the file from S3
    2. Run the Smart OCR Router
    3. Store full text + structured OCR JSON under parsed/{job_id}/{document_id}/
    4. append=False: return document_id (the creation chord callback advances
       the job). append=True: dispatch single-document extraction directly.
    """
    try:
        update_document(document_id, status="OCR_PROCESSING")
        publish_status(job_id, "APPENDING" if append else "OCR_PROCESSING", 5.0,
                       document_id=document_id)
        if not append:
            update_job(job_id, status="OCR_PROCESSING", progress=5.0)

        doc = get_document_field(document_id, "file_key", "filename")
        file_key = doc["file_key"]
        filename = doc["filename"]

        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, filename)
            s3.download_file(settings.s3_bucket, file_key, local_path)
            logger.info(f"Downloaded {file_key} → {local_path}")

            from app.providers.factory import get_ocr_provider

            ocr = get_ocr_provider()
            ocr_result = ocr.process_document(local_path)

            logger.info(
                f"OCR complete: {ocr_result.page_count} pages, "
                f"{sum(len(p.regions) for p in ocr_result.pages)} regions"
            )

            prefix = f"parsed/{job_id}/{document_id}"
            from app.core.storage import upload_file_to_s3

            upload_file_to_s3(
                file_bytes=ocr_result.full_text.encode("utf-8"),
                object_key=f"{prefix}/full_text.txt",
                content_type="text/plain",
            )

            import dataclasses

            ocr_data = {
                "page_count": ocr_result.page_count,
                "pages": [
                    {
                        "page_number": p.page_number,
                        "width": p.width,
                        "height": p.height,
                        "regions": [dataclasses.asdict(r) for r in p.regions],
                    }
                    for p in ocr_result.pages
                ],
            }
            upload_file_to_s3(
                file_bytes=json.dumps(ocr_data, indent=2).encode("utf-8"),
                object_key=f"{prefix}/ocr_result.json",
                content_type="application/json",
            )

        update_document(document_id, status="OCR_DONE", page_count=ocr_result.page_count)
        publish_status(job_id, "APPENDING" if append else "OCR_PROCESSING", 60.0,
                       document_id=document_id)

        if append:
            from app.worker.tasks.extract import run_extraction

            run_extraction.apply_async(args=[job_id], kwargs={"document_id": document_id})
            logger.info(f"Job {job_id}: append OCR done, dispatched extraction "
                        f"for document {document_id}")
        return document_id

    except Exception as exc:
        logger.exception(f"Job {job_id} document {document_id}: OCR failed: {exc}")
        update_document(
            document_id, status="FAILED", error_message=public_error_message(exc)
        )
        if append:
            # Failure isolation: the dataset stays live.
            update_job(job_id, status="COMPLETED", progress=100.0)
            publish_status(job_id, "COMPLETED", 100.0,
                           error_message=public_error_message(exc),
                           document_id=document_id)
            raise
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise self.retry(exc=exc, countdown=60)
```

Note the import line at the top of the file becomes:

```python
from app.worker.db import get_document_field, get_job_field, publish_status, update_document, update_job
```

(`get_job_field` stays — `ocr_complete` below uses it. The retry-on-append path deliberately raises without `self.retry`: re-running an append is a user action, not an automatic retry.)

- [ ] **Step 2: Add the creation chord callback to the same file**

```python
@celery_app.task(
    name="app.worker.tasks.ocr.ocr_complete",
    bind=True,
    queue="ocr",
)
def ocr_complete(self, document_ids: list[str], job_id: str):
    """Chord callback after every founding document finishes OCR.

    Routes FULL jobs to multi-document profiling and TARGETED jobs to RAG
    indexing (TARGETED is validated single-document at creation).
    """
    try:
        job = get_job_field(job_id, "job_type")
        total_pages = sum(
            d["page_count"] or 0
            for d in list_job_documents(job_id, "page_count")
        )
        update_job(job_id, status="OCR_PROCESSING", progress=75.0, page_count=total_pages)
        publish_status(job_id, "OCR_PROCESSING", 75.0)

        if job["job_type"] == "TARGETED":
            from app.worker.tasks.rag import index_document

            index_document.apply_async(args=[job_id, document_ids[0]])
            logger.info(f"Job {job_id}: OCR complete, dispatched indexing (TARGETED)")
        else:
            from app.worker.tasks.profile import profile_and_propose

            profile_and_propose.apply_async(args=[job_id])
            logger.info(f"Job {job_id}: OCR complete, dispatched profiling (FULL)")
    except Exception as exc:
        logger.exception(f"Job {job_id}: ocr_complete failed")
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise
```

Add `list_job_documents` to the worker-db import line. NOTE: `update_job(..., page_count=...)` writes the legacy job column — keep it until Task 12, then this kwarg is removed there.

- [ ] **Step 3: Dispatch the chord from the API via a patchable helper**

A bare `chord(...)(...)` call inside the endpoint would contact the Redis broker even in tests (monkeypatching `apply_async` does not intercept chord dispatch). Wrap it in a module-level helper so the integration conftest can replace one symbol.

In `apps/api/app/api/v1/jobs.py`, add near the top (after the router definition):

```python
def _dispatch_ocr(job: Job) -> None:
    """Fan OCR out across the job's documents; the chord callback advances the job."""
    from celery import chord, group

    from app.worker.tasks.ocr import ocr_complete, process_document

    chord(group(process_document.s(job.id, d.id) for d in job.documents))(
        ocr_complete.s(job.id)
    )
```

In `create_job`, replace the dispatch block (the `from app.worker.tasks.ocr import process_document` + `apply_async` lines) with:

```python
    _dispatch_ocr(job)
```

In `reject_model` (FULL branch), replace `process_document.apply_async(args=[job.id])` and its import with the same call:

```python
    _dispatch_ocr(job)
```

Then update `apps/api/tests/integration/conftest.py`: replace the existing

```python
    from app.worker.tasks import ocr as ocr_tasks

    monkeypatch.setattr(ocr_tasks.process_document, "apply_async", lambda *a, **k: None)
```

with

```python
    monkeypatch.setattr(jobs_module, "_dispatch_ocr", lambda job: None)
```

(`jobs_module` is already imported in that fixture for the S3 delete patches; place the new line beside them.)

- [ ] **Step 4: Fix the RAG task path**

In `apps/api/app/worker/tasks/rag.py`, change the task signature from `def index_document(self, job_id: str):` to `def index_document(self, job_id: str, document_id: str):` and the parsed-key line from `parsed_key = f"parsed/{job_id}/full_text.txt"` to:

```python
        parsed_key = f"parsed/{job_id}/{document_id}/full_text.txt"
```

- [ ] **Step 5: Update the failure callback**

In `apps/api/app/worker/callbacks.py` replace `_JOB_ID_ARG_INDEX` and add a document map:

```python
_JOB_ID_ARG_INDEX: dict[str, int] = {
    "app.worker.tasks.ocr.process_document": 0,
    "app.worker.tasks.ocr.ocr_complete": 1,
    "app.worker.tasks.extract.run_extraction": 0,
    "app.worker.tasks.extract.extract_table_chunk": 0,
    "app.worker.tasks.merge.merge_results": 1,
    "app.worker.tasks.reconcile.rebuild_dataset": 0,
    "app.worker.tasks.translate.translate_and_provision": 0,
    "app.worker.tasks.rag.index_document": 0,
}

# Tasks that carry a document_id positional arg (worker-crash safety: the
# in-flight document must not strand in OCR_PROCESSING/EXTRACTING).
_DOCUMENT_ID_ARG_INDEX: dict[str, int] = {
    "app.worker.tasks.ocr.process_document": 1,
    "app.worker.tasks.extract.extract_table_chunk": 1,
    "app.worker.tasks.rag.index_document": 1,
}
```

and extend `on_task_failure` after the job update:

```python
    task_name = getattr(sender, "name", str(sender))
    doc_idx = _DOCUMENT_ID_ARG_INDEX.get(task_name)
    document_id = None
    if doc_idx is not None and args and len(args) > doc_idx:
        document_id = str(args[doc_idx])
    document_id = document_id or kwargs.get("document_id")
    if document_id:
        try:
            from app.worker.db import update_document

            update_document(document_id, status="FAILED", error_message=error_msg)
        except Exception:
            logger.exception(
                f"Document {document_id}: failed to update status in failure callback"
            )
```

(`app.worker.tasks.reconcile.rebuild_dataset` is registered ahead of Task 9 creating it — a map entry for a not-yet-existing task name is inert.)

NOTE on append-mode hard failures: the signal handler cannot know whether the failed task belonged to an append, so a hard crash during an append marks the job FAILED rather than returning it to COMPLETED. That is acceptable — no data is lost (buckets persist) and `POST /jobs/{id}/rebuild` (Task 11) is the documented recovery path.

- [ ] **Step 6: Write Tier 2 tests**

```python
# apps/api/tests/test_ocr_task.py
"""Tier 2: per-document OCR task wiring (faked S3/OCR/DB)."""

from dataclasses import dataclass, field

import pytest

from app.worker.tasks import ocr as ocr_task


@dataclass
class _FakeOCRResult:
    page_count: int = 2
    full_text: str = "hello"
    pages: list = field(default_factory=list)


class _Recorder:
    def __init__(self):
        self.job_updates = []
        self.doc_updates = []
        self.published = []
        self.uploaded_keys = []


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(
        ocr_task, "update_job", lambda job_id, **kw: rec.job_updates.append(kw)
    )
    monkeypatch.setattr(
        ocr_task,
        "update_document",
        lambda doc_id, **kw: rec.doc_updates.append((doc_id, kw)),
    )
    monkeypatch.setattr(
        ocr_task,
        "publish_status",
        lambda job_id, status, progress, **kw: rec.published.append((status, kw)),
    )
    monkeypatch.setattr(
        ocr_task,
        "get_document_field",
        lambda doc_id, *cols: {"file_key": "uploads/u/x/a.pdf", "filename": "a.pdf"},
    )

    class _FakeS3:
        def download_file(self, bucket, key, path):
            pass

    monkeypatch.setattr("app.core.storage.get_s3_client", lambda: _FakeS3())
    monkeypatch.setattr(
        "app.core.storage.upload_file_to_s3",
        lambda file_bytes, object_key, content_type: rec.uploaded_keys.append(object_key),
    )

    class _FakeProvider:
        def process_document(self, path):
            return _FakeOCRResult()

    monkeypatch.setattr("app.providers.factory.get_ocr_provider", lambda: _FakeProvider())
    return rec


def test_create_mode_writes_per_document_paths_and_returns_doc_id(recorder, monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "app.worker.tasks.extract.run_extraction",
        type("T", (), {"apply_async": staticmethod(lambda **kw: dispatched.append(kw))}),
    )
    result = ocr_task.process_document.run("job-1", "doc-1")
    assert result == "doc-1"
    assert recorder.uploaded_keys == [
        "parsed/job-1/doc-1/full_text.txt",
        "parsed/job-1/doc-1/ocr_result.json",
    ]
    assert ("doc-1", {"status": "OCR_DONE", "page_count": 2}) in recorder.doc_updates
    assert dispatched == []  # chord callback advances creation, not this task


def test_append_mode_dispatches_single_document_extraction(recorder, monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "app.worker.tasks.extract.run_extraction",
        type("T", (), {"apply_async": staticmethod(lambda **kw: dispatched.append(kw))}),
    )
    ocr_task.process_document.run("job-1", "doc-2", append=True)
    assert dispatched == [{"args": ["job-1"], "kwargs": {"document_id": "doc-2"}}]


def test_append_failure_keeps_job_completed(recorder, monkeypatch):
    class _BoomProvider:
        def process_document(self, path):
            raise RuntimeError("ocr exploded")

    monkeypatch.setattr("app.providers.factory.get_ocr_provider", lambda: _BoomProvider())
    with pytest.raises(RuntimeError):
        ocr_task.process_document.run("job-1", "doc-3", append=True)
    assert any(
        d == "doc-3" and kw.get("status") == "FAILED" for d, kw in recorder.doc_updates
    )
    assert {"status": "COMPLETED", "progress": 100.0} in recorder.job_updates
```

NOTE for the implementer: `task.run(...)` executes the task body synchronously without a broker. The fake `run_extraction.apply_async` monkeypatch must target where the task object lives (`app.worker.tasks.extract.run_extraction`) because `process_document` imports it lazily inside the function — patching the attribute on the *module* it is imported from works for lazy imports. If the lazy `from app.worker.tasks.extract import run_extraction` resists monkeypatching that way (it binds at call time, so it should not), adapt by patching `run_extraction.apply_async` directly via `monkeypatch.setattr("app.worker.tasks.extract.run_extraction.apply_async", ...)` and report the adjustment.

- [ ] **Step 7: Run, lint, commit**

```bash
uv run pytest tests/test_ocr_task.py -v   # 3 PASS
uv run pytest -q                          # all green
uv run ruff check app/worker/tasks/ocr.py app/worker/tasks/rag.py app/worker/callbacks.py app/api/v1/jobs.py tests/test_ocr_task.py tests/integration/conftest.py
uv run ruff format app/worker/tasks/ocr.py app/worker/tasks/rag.py app/worker/callbacks.py app/api/v1/jobs.py tests/test_ocr_task.py tests/integration/conftest.py
git add app/worker/tasks/ocr.py app/worker/tasks/rag.py app/worker/callbacks.py app/api/v1/jobs.py tests/test_ocr_task.py tests/integration/conftest.py
git commit -m "feat: per-document OCR with creation chord and append entry point"
```

---

### Task 8: Multi-document profiling

**Files:**
- Modify: `apps/api/app/worker/tasks/profile.py`
- Modify: `apps/api/app/schemas/extraction_model.py` (one additive field)
- Test: `apps/api/tests/test_profile_task.py`

- [ ] **Step 1: Add the per-document sampling field to DocumentProfile**

In `apps/api/app/schemas/extraction_model.py`, inside `DocumentProfile`, after `sampled_pages: list[int]`:

```python
    sampled_pages_by_document: dict[str, list[int]] = Field(
        default_factory=dict,
        description="Document id → sampled page numbers (multi-file datasets).",
    )
```

(Additive with a default — previously stored profiles still validate.)

- [ ] **Step 2: Rewrite the profiling task body**

Replace the body of `profile_and_propose` in `apps/api/app/worker/tasks/profile.py` (keep decorator/signature/except block; the import line gains `list_job_documents`):

```python
    try:
        publish_status(job_id, "PROFILING", 0.0)
        update_job(job_id, status="PROFILING", progress=0.0)

        # 1. Load every OCR-complete document's OCR JSON from S3.
        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        documents = [
            d
            for d in list_job_documents(job_id, "id", "filename", "page_count", "status")
            if d["status"] in ("OCR_DONE", "EXTRACTED")
        ]
        if not documents:
            raise ValueError("no OCR-complete documents to profile")

        ocr_by_doc: dict[str, dict] = {}
        for d in documents:
            key = f"parsed/{job_id}/{d['id']}/ocr_result.json"
            response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
            ocr_by_doc[d["id"]] = json.loads(response["Body"].read().decode("utf-8"))

        publish_status(job_id, "PROFILING", 20.0)

        # 2. Split the sampling budget and profile each document.
        from app.services.consolidation import allocate_sampling_budget
        from app.services.profiling import (
            MAX_SAMPLED_PAGES,
            build_profile_context,
            profile_document,
        )

        page_counts = {d["id"]: d["page_count"] or 0 for d in documents}
        budgets = allocate_sampling_budget(page_counts, budget=MAX_SAMPLED_PAGES)

        sampled_by_doc: dict[str, list[int]] = {}
        merged_histogram: dict[str, int] = {}
        context_blocks: list[str] = []
        filenames = {d["id"]: d["filename"] for d in documents}
        for doc_id in sorted(ocr_by_doc):
            sampled, histogram = profile_document(
                ocr_by_doc[doc_id], budget=budgets.get(doc_id)
            )
            sampled_by_doc[doc_id] = sampled
            for rtype, count in histogram.items():
                merged_histogram[rtype] = merged_histogram.get(rtype, 0) + count
            block = build_profile_context(sampled, ocr_by_doc[doc_id])
            context_blocks.append(
                f"=== Document: {filenames[doc_id]} ===\n{block}"
            )
        context_text = "\n\n".join(context_blocks)
        total_pages = sum(page_counts.values())
        all_sampled = sorted({p for pages in sampled_by_doc.values() for p in pages})

        logger.info(
            f"Job {job_id}: profiled {len(documents)} document(s), "
            f"budgets={budgets}, regions={merged_histogram}"
        )

        publish_status(job_id, "PROFILING", 50.0)

        # 3. LLM proposes the DatabaseModel from the combined context.
        from app.providers.factory import get_llm_provider

        llm = get_llm_provider()
        proposed_model = llm.generate_model(
            document_text=context_text,
            profile=None,
            num_pages=total_pages,
        )

        publish_status(job_id, "PROFILING", 80.0)

        document_profile = DocumentProfile(
            total_pages=total_pages,
            sampled_pages=all_sampled,
            sampled_pages_by_document=sampled_by_doc,
            region_summary=merged_histogram,
            sections=[],  # MVP: profiling does not produce sections; review UI handles routing
            recommended_extraction_type=proposed_model.extraction_type,
            rationale=(
                f"Sampled {sum(len(v) for v in sampled_by_doc.values())} pages "
                f"across {len(documents)} document(s) ({total_pages} total pages). "
                f"LLM proposed {len(proposed_model.tables)} table(s) "
                f"with {len(proposed_model.relationships)} relationship(s)."
            ),
        )

        update_job(
            job_id,
            status="MODEL_PROPOSED",
            progress=100.0,
            document_profile=json.dumps(document_profile.model_dump()),
            proposed_model=json.dumps(proposed_model.model_dump()),
        )
        publish_status(job_id, "MODEL_PROPOSED", 100.0)

        logger.info(
            f"Job {job_id}: profiling complete, "
            f"extraction_type={proposed_model.extraction_type}, "
            f"tables={[t.table_name for t in proposed_model.tables]}"
        )
```

- [ ] **Step 3: Write Tier 2 tests**

```python
# apps/api/tests/test_profile_task.py
"""Tier 2: multi-document profiling task (faked S3/LLM/DB)."""

import io
import json

import pytest

from app.worker.tasks import profile as profile_task
from tests.factories import make_column, make_model, make_table


def _ocr_json(pages: int) -> dict:
    return {
        "page_count": pages,
        "pages": [
            {"page_number": n, "regions": [{"region_type": "text", "text": f"p{n}"}]}
            for n in range(1, pages + 1)
        ],
    }


@pytest.fixture
def wired(monkeypatch):
    state = {"job_updates": [], "model_calls": []}
    docs = [
        {"id": "doc-a", "filename": "a.pdf", "page_count": 4, "status": "OCR_DONE"},
        {"id": "doc-b", "filename": "b.pdf", "page_count": 20, "status": "OCR_DONE"},
    ]
    monkeypatch.setattr(profile_task, "list_job_documents", lambda job_id, *c: docs)
    monkeypatch.setattr(profile_task, "update_job",
                        lambda job_id, **kw: state["job_updates"].append(kw))
    monkeypatch.setattr(profile_task, "publish_status", lambda *a, **kw: None)

    ocr_payloads = {"doc-a": _ocr_json(4), "doc-b": _ocr_json(20)}

    class _FakeS3:
        def get_object(self, Bucket, Key):
            doc_id = Key.split("/")[2]
            body = json.dumps(ocr_payloads[doc_id]).encode()
            return {"Body": io.BytesIO(body)}

    monkeypatch.setattr("app.core.storage.get_s3_client", lambda: _FakeS3())

    proposed = make_model([make_table("t", [make_column("a", pk=True)])])

    class _FakeLLM:
        def generate_model(self, document_text, profile, num_pages):
            state["model_calls"].append({"text": document_text, "pages": num_pages})
            return proposed

    monkeypatch.setattr("app.providers.factory.get_llm_provider", lambda: _FakeLLM())
    return state


def test_profiles_all_documents_with_split_budget(wired):
    profile_task.profile_and_propose.run("job-1")

    final = wired["job_updates"][-1]
    assert final["status"] == "MODEL_PROPOSED"
    profile = json.loads(final["document_profile"])
    assert profile["total_pages"] == 24
    assert set(profile["sampled_pages_by_document"]) == {"doc-a", "doc-b"}
    # doc-a (4 pages) keeps everything; doc-b gets the rest of the 15-budget.
    assert profile["sampled_pages_by_document"]["doc-a"] == [1, 2, 3, 4]
    assert len(profile["sampled_pages_by_document"]["doc-b"]) == 11

    call = wired["model_calls"][0]
    assert call["pages"] == 24
    assert "=== Document: a.pdf ===" in call["text"]
    assert "=== Document: b.pdf ===" in call["text"]
```

NOTE on the doc-a expectation: `allocate_sampling_budget({"doc-a": 4, "doc-b": 20}, budget=15)` gives doc-a `min(3,4)=3` floor, doc-b 3, remaining 9 distributed by headroom — doc-b headroom 17, doc-a headroom 1 → doc-b takes pages until headroom ties: final `{"doc-a": 4, "doc-b": 11}`. Verify by running the function in a REPL during implementation; if the deterministic split differs, fix the test numbers to the actual values **and** confirm they still sum to 15 with doc-a == 4.

- [ ] **Step 4: Run, lint, commit**

```bash
uv run pytest tests/test_profile_task.py -v   # 1 PASS
uv run pytest -q
uv run ruff check app/worker/tasks/profile.py app/schemas/extraction_model.py tests/test_profile_task.py
uv run ruff format app/worker/tasks/profile.py app/schemas/extraction_model.py tests/test_profile_task.py
git add app/worker/tasks/profile.py app/schemas/extraction_model.py tests/test_profile_task.py
git commit -m "feat: unified model discovery across all dataset documents"
```

---

### Task 9: rebuild_dataset (reconcile.py rewrite)

**Files:**
- Modify: `apps/api/app/worker/tasks/reconcile.py` (full rewrite of the task)
- Test: `apps/api/tests/test_rebuild_task.py`

- [ ] **Step 1: Rewrite reconcile.py**

Replace the entire contents of `apps/api/app/worker/tasks/reconcile.py`:

```python
"""ParseGrid — Dataset rebuild task (Dataset Consolidation).

Unions every EXTRACTED document's stored buckets, runs deterministic
reconciliation over the union, persists the reconciled extracted_data on the
job, and dispatches translate_and_provision (which drop-recreates the output
schema). The output database is a cache; per-document buckets are the truth.

Legacy jobs (pre-consolidation) stored job-level buckets in S3 at
extracted/{job_id}/merged_buckets.json — when an EXTRACTED document has no
stored buckets, that file is adopted as the founding document's buckets.
"""

from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.schemas.extraction_model import DatabaseModel
from app.services.safe_errors import public_error_message
from app.worker.celery_app import celery_app
from app.worker.db import (
    get_job_field,
    list_job_documents,
    publish_status,
    update_document,
    update_job,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.worker.tasks.reconcile.rebuild_dataset",
    bind=True,
    queue="merge",
)
def rebuild_dataset(self, job_id: str):
    """Re-reconcile the union of all EXTRACTED documents and re-provision."""
    try:
        publish_status(job_id, "RECONCILING", 0.0)
        update_job(job_id, status="RECONCILING", progress=0.0)

        job = get_job_field(job_id, "locked_model")
        locked_raw = job["locked_model"]
        if isinstance(locked_raw, str):
            locked_raw = json.loads(locked_raw)
        if not locked_raw:
            raise ValueError("locked_model missing — cannot rebuild")
        locked_model = DatabaseModel.model_validate(locked_raw)

        doc_buckets = _load_document_buckets(job_id)
        if not doc_buckets:
            raise ValueError("no extracted documents with buckets — nothing to rebuild")

        from app.services.consolidation import union_buckets
        from app.services.reconciliation import reconcile_model

        bucketed_rows, chunk_pages = union_buckets(doc_buckets)
        finalized, run_notes = reconcile_model(
            bucketed_rows=bucketed_rows,
            chunk_pages_by_index=chunk_pages,
            locked_model=locked_model,
        )

        publish_status(job_id, "RECONCILING", 60.0)

        update_job(
            job_id,
            status="RECONCILING",
            progress=80.0,
            extracted_data=json.dumps(finalized, default=str),
        )

        from app.core.storage import upload_file_to_s3

        upload_file_to_s3(
            file_bytes=json.dumps(
                {
                    "notes": run_notes,
                    "documents": [doc_id for doc_id, _ in doc_buckets],
                    "table_counts": {t: len(r) for t, r in finalized.items()},
                },
                indent=2,
            ).encode("utf-8"),
            object_key=f"extracted/{job_id}/reconciliation_notes.json",
            content_type="application/json",
        )

        publish_status(job_id, "RECONCILING", 100.0)
        logger.info(
            f"Job {job_id}: rebuilt from {len(doc_buckets)} document(s), "
            f"counts={{ {', '.join(f'{t}={len(r)}' for t, r in finalized.items())} }}"
        )

        from app.worker.tasks.translate import translate_and_provision

        translate_and_provision.apply_async(args=[job_id])

    except Exception as exc:
        logger.exception(f"Job {job_id}: rebuild failed")
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise


def _load_document_buckets(job_id: str) -> list[tuple[str, dict]]:
    """(document_id, buckets) for every EXTRACTED document, oldest first.

    Falls back to the legacy job-level S3 buckets for backfilled documents
    that predate per-document storage; the adopted buckets are persisted on
    the document so the fallback runs at most once.
    """
    docs = list_job_documents(job_id, "id", "status", "extracted_buckets")
    result: list[tuple[str, dict]] = []
    for doc in docs:
        if doc["status"] != "EXTRACTED":
            continue
        buckets = doc["extracted_buckets"]
        if isinstance(buckets, str):
            buckets = json.loads(buckets)
        if not buckets:
            buckets = _adopt_legacy_buckets(job_id, doc["id"])
        if buckets:
            result.append((doc["id"], buckets))
    return result


def _adopt_legacy_buckets(job_id: str, document_id: str) -> dict | None:
    from app.core.storage import get_s3_client

    s3 = get_s3_client()
    key = f"extracted/{job_id}/merged_buckets.json"
    try:
        response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        logger.warning(
            f"Job {job_id}: document {document_id} has no buckets and no legacy "
            f"{key} — it cannot contribute to rebuilds"
        )
        return None
    buckets = json.loads(response["Body"].read().decode("utf-8"))
    update_document(document_id, extracted_buckets=json.dumps(buckets))
    logger.info(f"Job {job_id}: adopted legacy buckets for document {document_id}")
    return buckets
```

- [ ] **Step 2: Write Tier 2 tests**

```python
# apps/api/tests/test_rebuild_task.py
"""Tier 2: rebuild_dataset unions document buckets and dispatches translate."""

import json

import pytest

from app.worker.tasks import reconcile as rebuild_task
from tests.factories import make_column, make_model, make_table

MODEL = make_model(
    [make_table("invoices", [make_column("invoice_number", pk=True)])]
)


def _buckets(doc: str, numbers: list[str]) -> dict:
    return {
        "tables": {
            "invoices": {
                "rows": [
                    {"invoice_number": n, "__chunk_index": f"{doc}:0"} for n in numbers
                ],
                "chunk_pages": {f"{doc}:0": [1]},
            }
        }
    }


@pytest.fixture
def wired(monkeypatch):
    state = {"job_updates": [], "dispatched": [], "uploads": []}
    monkeypatch.setattr(
        rebuild_task, "get_job_field",
        lambda job_id, *c: {"locked_model": MODEL.model_dump()},
    )
    monkeypatch.setattr(rebuild_task, "update_job",
                        lambda job_id, **kw: state["job_updates"].append(kw))
    monkeypatch.setattr(rebuild_task, "publish_status", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.core.storage.upload_file_to_s3",
        lambda file_bytes, object_key, content_type: state["uploads"].append(object_key),
    )
    monkeypatch.setattr(
        "app.worker.tasks.translate.translate_and_provision",
        type("T", (), {"apply_async": staticmethod(
            lambda args: state["dispatched"].append(args))}),
    )
    return state


def test_unions_documents_and_dedupes_across_files(wired, monkeypatch):
    docs = [
        {"id": "d1", "status": "EXTRACTED", "extracted_buckets": _buckets("d1", ["1", "2"])},
        {"id": "d2", "status": "EXTRACTED", "extracted_buckets": _buckets("d2", ["2", "3"])},
        {"id": "d3", "status": "REJECTED", "extracted_buckets": _buckets("d3", ["9"])},
    ]
    monkeypatch.setattr(rebuild_task, "list_job_documents", lambda job_id, *c: docs)

    rebuild_task.rebuild_dataset.run("job-1")

    final = next(u for u in wired["job_updates"] if "extracted_data" in u)
    data = json.loads(final["extracted_data"])
    numbers = sorted(r["invoice_number"] for r in data["invoices"])
    assert numbers == ["1", "2", "3"]  # "2" deduped across documents; d3 excluded
    assert wired["dispatched"] == [["job-1"]]


def test_legacy_document_adopts_job_level_s3_buckets(wired, monkeypatch):
    docs = [{"id": "d1", "status": "EXTRACTED", "extracted_buckets": None}]
    monkeypatch.setattr(rebuild_task, "list_job_documents", lambda job_id, *c: docs)
    adopted = []
    monkeypatch.setattr(
        rebuild_task, "update_document",
        lambda doc_id, **kw: adopted.append(doc_id),
    )

    import io

    legacy = _buckets("0", ["7"])

    class _FakeS3:
        def get_object(self, Bucket, Key):
            assert Key == "extracted/job-1/merged_buckets.json"
            return {"Body": io.BytesIO(json.dumps(legacy).encode())}

    monkeypatch.setattr("app.core.storage.get_s3_client", lambda: _FakeS3())

    rebuild_task.rebuild_dataset.run("job-1")

    assert adopted == ["d1"]
    final = next(u for u in wired["job_updates"] if "extracted_data" in u)
    assert json.loads(final["extracted_data"])["invoices"][0]["invoice_number"] == "7"


def test_no_extracted_documents_fails_loud(wired, monkeypatch):
    monkeypatch.setattr(rebuild_task, "list_job_documents", lambda job_id, *c: [])
    with pytest.raises(ValueError):
        rebuild_task.rebuild_dataset.run("job-1")
    assert any(kw.get("status") == "FAILED" for kw in wired["job_updates"])
```

- [ ] **Step 3: Run, lint, commit**

```bash
uv run pytest tests/test_rebuild_task.py -v   # 3 PASS
uv run pytest -q
uv run ruff check app/worker/tasks/reconcile.py tests/test_rebuild_task.py
uv run ruff format app/worker/tasks/reconcile.py tests/test_rebuild_task.py
git add app/worker/tasks/reconcile.py tests/test_rebuild_task.py
git commit -m "feat: rebuild_dataset reconciles the union of document buckets"
```

NOTE: `merge.py` still references the old `reconcile_and_translate` until Task 10 — the import inside `merge_results` would fail at runtime, but no test exercises it; Task 10 rewires it. (The Foundation `task_failure` map was already updated in Task 7.)

---

### Task 10: Per-document extraction fan-out, per-document buckets, compat gate

**Files:**
- Modify: `apps/api/app/worker/tasks/extract.py`
- Modify: `apps/api/app/worker/tasks/merge.py`
- Modify: `apps/api/app/core/config.py` (one setting)
- Test: `apps/api/tests/test_merge_task.py`

- [ ] **Step 1: Add the threshold setting**

In `apps/api/app/core/config.py`, after the `# --- Connection-test guard ---` block:

```python
    # --- Dataset consolidation ---
    # Share of appended-document rows allowed to miss primary-key components
    # before the append pauses for human review.
    append_max_pk_null_ratio: float = 0.5
```

- [ ] **Step 2: Rework `extract.py`**

Change `extract_table_chunk`'s signature and return (document-aware chunk keys):

```python
def extract_table_chunk(
    self,
    job_id: str,
    document_id: str,
    table_name: str,
    chunk_key: str,
    chunk_text: str,
    pages: list[int],
    table_def_json: dict,
    link_targets_json: list[dict],
):
```

with the return value:

```python
    return {
        "document_id": document_id,
        "table_name": table_name,
        "chunk_key": chunk_key,
        "rows": rows,
        "pages": pages,
        "tokens": response.usage,
    }
```

(update the log line to use `chunk={chunk_key}`).

Change `run_extraction` to be document-scoped:

```python
@celery_app.task(
    name="app.worker.tasks.extract.run_extraction",
    bind=True,
    queue="extraction",
)
def run_extraction(self, job_id: str, document_id: str | None = None):
    """Orchestrate the per-table Map phase across the job's documents.

    document_id=None  → creation: extract every OCR_DONE document.
    document_id set   → append: extract just that document; merge runs the
                        compatibility gate before rebuilding.
    """
    try:
        append = document_id is not None
        status = "APPENDING" if append else "EXTRACTING"
        publish_status(job_id, status, 0.0, document_id=document_id)
        update_job(job_id, status=status, progress=0.0)

        job = get_job_field(job_id, "locked_model", "job_type", "target_chunks", "section_map")
        locked_model_raw = _coerce_json(job["locked_model"])
        if not locked_model_raw:
            raise ValueError("locked_model is empty — cannot extract")
        locked_model = DatabaseModel.model_validate(locked_model_raw)

        job_type = job["job_type"]
        target_chunks_raw = _coerce_json(job["target_chunks"])
        section_map_raw = _coerce_json(job["section_map"]) or []
        sections = [SectionCandidate.model_validate(s) for s in section_map_raw]

        if append:
            documents = [{"id": document_id}]
        else:
            documents = [
                d
                for d in list_job_documents(job_id, "id", "status")
                if d["status"] == "OCR_DONE"
            ]
        if not documents:
            raise ValueError("no documents ready for extraction")

        from app.services.extraction import chunk_text

        # Build per-document chunks, keys prefixed with the document id.
        doc_chunks: list[tuple[str, dict]] = []  # (document_id, chunk)
        if job_type == "TARGETED" and target_chunks_raw:
            doc_id = documents[0]["id"]
            for i, chunk in enumerate(target_chunks_raw):
                doc_chunks.append(
                    (
                        doc_id,
                        {
                            "chunk_key": f"{doc_id}:{i}",
                            "text": chunk["text"],
                            "pages": [chunk.get("page_number")]
                            if chunk.get("page_number")
                            else [],
                        },
                    )
                )
            logger.info(f"Job {job_id}: TARGETED mode — {len(doc_chunks)} retrieved chunks")
        else:
            from app.core.storage import get_s3_client

            s3 = get_s3_client()
            for d in documents:
                response = s3.get_object(
                    Bucket=settings.s3_bucket,
                    Key=f"parsed/{job_id}/{d['id']}/full_text.txt",
                )
                full_text = response["Body"].read().decode("utf-8")
                for ch in chunk_text(full_text, chunk_size=3000, overlap=500):
                    doc_chunks.append(
                        (
                            d["id"],
                            {
                                "chunk_key": f"{d['id']}:{ch['chunk_index']}",
                                "text": ch["text"],
                                "pages": ch.get("pages", []),
                            },
                        )
                    )
            logger.info(
                f"Job {job_id}: FULL mode — {len(doc_chunks)} chunks across "
                f"{len(documents)} document(s)"
            )

        for d in documents:
            update_document(d["id"], status="EXTRACTING")

        publish_status(job_id, status, 10.0, document_id=document_id)

        signatures = []
        for table in locked_model.tables:
            allowed_pages = _allowed_pages_for_table(table.table_name, sections)
            link_targets = [
                rel
                for rel in locked_model.relationships
                if rel.source_table == table.table_name and rel.enabled
            ]
            link_targets_json = [r.model_dump() for r in link_targets]
            table_def_json = table.model_dump()

            for doc_id, ch in doc_chunks:
                if allowed_pages is not None and not _chunk_in_pages(ch, allowed_pages):
                    continue
                signatures.append(
                    extract_table_chunk.s(
                        job_id,
                        doc_id,
                        table.table_name,
                        ch["chunk_key"],
                        ch["text"],
                        ch.get("pages", []),
                        table_def_json,
                        link_targets_json,
                    )
                )

        if not signatures:
            raise ValueError("no extraction tasks scheduled — locked_model has no tables or chunks")

        from app.worker.tasks.merge import merge_results

        chord(group(*signatures))(merge_results.s(job_id, document_id))
        logger.info(
            f"Job {job_id}: extraction chord dispatched with {len(signatures)} chunk tasks "
            f"across {len(locked_model.tables)} tables"
        )

    except Exception as exc:
        logger.exception(f"Job {job_id}: extraction orchestration failed")
        if document_id is not None:
            update_document(
                document_id, status="FAILED", error_message=public_error_message(exc)
            )
            update_job(job_id, status="COMPLETED", progress=100.0)
            publish_status(job_id, "COMPLETED", 100.0,
                           error_message=public_error_message(exc),
                           document_id=document_id)
        else:
            publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
            update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise
```

Replace `_filter_chunks_by_pages` with the single-chunk predicate (and delete the old function):

```python
def _chunk_in_pages(chunk: dict[str, Any], allowed_pages: set[int]) -> bool:
    """True when the chunk's pages overlap `allowed_pages` (empty set → False)."""
    if not allowed_pages:
        return False
    return any(p in allowed_pages for p in (chunk.get("pages") or []))
```

Update the worker-db import line: `from app.worker.db import get_job_field, list_job_documents, publish_status, update_document, update_job`.

- [ ] **Step 3: Rework `merge.py`**

Replace `merge_results` with:

```python
@celery_app.task(
    name="app.worker.tasks.merge.merge_results",
    bind=True,
    queue="merge",
)
def merge_results(self, chunk_results: list[dict], job_id: str, append_document_id: str | None = None):
    """Bucket chunk results per document, persist each document's buckets,
    then either run the append compatibility gate or rebuild the dataset.

    Each chunk result is
    `{document_id, table_name, chunk_key, rows, pages, tokens}`. Buckets are
    stored on the Document row in the shape
    `{"tables": {tbl: {"rows": [...], "chunk_pages": {chunk_key: [pages]}}}}`.
    """
    try:
        append = append_document_id is not None
        status = "APPENDING" if append else "MERGING"
        publish_status(job_id, status, 0.0, document_id=append_document_id)
        if not append:
            update_job(job_id, status="MERGING", progress=0.0)

        per_doc: dict[str, dict] = {}
        for entry in chunk_results:
            if not isinstance(entry, dict):
                continue
            doc_id = entry.get("document_id")
            table_name = entry.get("table_name")
            chunk_key = entry.get("chunk_key")
            rows = entry.get("rows") or []
            pages = entry.get("pages") or []
            if not doc_id or not table_name:
                continue
            tables = per_doc.setdefault(doc_id, {"tables": {}})["tables"]
            bucket = tables.setdefault(table_name, {"rows": [], "chunk_pages": {}})
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row = dict(row)
                row["__chunk_index"] = chunk_key
                bucket["rows"].append(row)
            if chunk_key is not None:
                bucket["chunk_pages"][chunk_key] = list(pages)

        for doc_id, payload in per_doc.items():
            update_document(
                doc_id,
                status="EXTRACTED",
                extracted_buckets=json.dumps(payload, default=str),
            )

        total_rows = sum(
            len(b["rows"])
            for payload in per_doc.values()
            for b in payload["tables"].values()
        )
        logger.info(
            f"Job {job_id}: merged {total_rows} rows across {len(per_doc)} document(s)"
        )

        if append:
            _run_compat_gate(job_id, append_document_id, per_doc.get(append_document_id))
            return

        publish_status(job_id, "MERGING", 100.0)
        from app.worker.tasks.reconcile import rebuild_dataset

        rebuild_dataset.apply_async(args=[job_id])

    except Exception as exc:
        logger.exception(f"Job {job_id}: merge failed")
        publish_status(job_id, "FAILED", 0.0, error_message=public_error_message(exc))
        update_job(job_id, status="FAILED", error_message=public_error_message(exc))
        raise


def _run_compat_gate(job_id: str, document_id: str, payload: dict | None) -> None:
    """Score the appended document against the locked model and route."""
    from app.schemas.extraction_model import DatabaseModel
    from app.services.consolidation import build_compat_report

    job = get_job_field(job_id, "locked_model", "document_profile")
    locked_raw = job["locked_model"]
    if isinstance(locked_raw, str):
        locked_raw = json.loads(locked_raw)
    locked_model = DatabaseModel.model_validate(locked_raw)

    profile_raw = job["document_profile"]
    if isinstance(profile_raw, str):
        profile_raw = json.loads(profile_raw)
    dataset_histogram = (profile_raw or {}).get("region_summary")

    tables = (payload or {}).get("tables") or {}
    buckets = {t: b.get("rows") or [] for t, b in tables.items()}
    report = build_compat_report(
        buckets,
        locked_model,
        max_pk_null_ratio=settings.append_max_pk_null_ratio,
        dataset_histogram=dataset_histogram,
        document_histogram=_document_histogram(job_id, document_id),
    )
    update_document(document_id, compat_report=json.dumps(report))

    if report["needs_review"]:
        update_job(job_id, status="AWAITING_APPEND_REVIEW", progress=50.0)
        publish_status(
            job_id, "AWAITING_APPEND_REVIEW", 50.0, document_id=document_id
        )
        logger.info(
            f"Job {job_id}: append {document_id} paused for review: {report['reasons']}"
        )
        return

    from app.worker.tasks.reconcile import rebuild_dataset

    rebuild_dataset.apply_async(args=[job_id])
    logger.info(f"Job {job_id}: append {document_id} compatible — rebuilding")


def _document_histogram(job_id: str, document_id: str) -> dict[str, int] | None:
    """Region-type histogram of the appended document (flag-only drift layer).

    Best-effort: any S3/parse failure returns None — drift recording must
    never break the gate.
    """
    try:
        from collections import Counter

        from app.core.storage import get_s3_client

        s3 = get_s3_client()
        response = s3.get_object(
            Bucket=settings.s3_bucket,
            Key=f"parsed/{job_id}/{document_id}/ocr_result.json",
        )
        ocr_json = json.loads(response["Body"].read().decode("utf-8"))
        histogram: Counter[str] = Counter()
        for page in ocr_json.get("pages") or []:
            for region in page.get("regions") or []:
                histogram[region.get("region_type") or "unknown"] += 1
        return dict(histogram)
    except Exception:
        logger.warning(
            f"Job {job_id}: could not build drift histogram for {document_id}",
            exc_info=True,
        )
        return None
```

Update merge.py's imports: `from app.worker.db import get_job_field, publish_status, update_document, update_job` and `from app.core.config import settings` at the top (then drop the function-local `from app.core.config import settings as app_settings` in `_run_compat_gate` and use `settings.append_max_pk_null_ratio` directly). Remove the now-unused `defaultdict` import and the S3 `merged_buckets.json` upload (per-document buckets in Postgres replace it; the legacy file remains readable for old jobs via Task 9's fallback).

The Tier 2 tests for the gate must also fake the histogram loader so they stay S3-free — add this line inside the `wired` fixture of `tests/test_merge_task.py`:

```python
    monkeypatch.setattr(merge_task, "_document_histogram", lambda job_id, doc_id: None)
```

- [ ] **Step 4: Write Tier 2 tests**

```python
# apps/api/tests/test_merge_task.py
"""Tier 2: merge buckets per document; append compatibility gate routing."""

import json

import pytest

from app.worker.tasks import merge as merge_task
from tests.factories import make_column, make_model, make_table

MODEL = make_model([make_table("invoices", [make_column("invoice_number", pk=True)])])


def _chunk_result(doc: str, key: str, rows: list[dict]) -> dict:
    return {
        "document_id": doc,
        "table_name": "invoices",
        "chunk_key": key,
        "rows": rows,
        "pages": [1],
        "tokens": {},
    }


@pytest.fixture
def wired(monkeypatch):
    state = {"doc_updates": [], "job_updates": [], "rebuilds": [], "published": []}
    monkeypatch.setattr(
        merge_task, "update_document",
        lambda doc_id, **kw: state["doc_updates"].append((doc_id, kw)),
    )
    monkeypatch.setattr(
        merge_task, "update_job",
        lambda job_id, **kw: state["job_updates"].append(kw),
    )
    monkeypatch.setattr(
        merge_task, "publish_status",
        lambda job_id, status, progress, **kw: state["published"].append(status),
    )
    monkeypatch.setattr(
        merge_task, "get_job_field",
        lambda job_id, *c: {
            "locked_model": MODEL.model_dump(),
            "document_profile": {"region_summary": {"text": 10}},
        },
    )
    monkeypatch.setattr(
        "app.worker.tasks.reconcile.rebuild_dataset",
        type("T", (), {"apply_async": staticmethod(
            lambda args: state["rebuilds"].append(args))}),
    )
    return state


def test_creation_merge_stores_buckets_per_document_and_rebuilds(wired):
    results = [
        _chunk_result("d1", "d1:0", [{"invoice_number": "1"}]),
        _chunk_result("d2", "d2:0", [{"invoice_number": "2"}]),
    ]
    merge_task.merge_results.run(results, "job-1")

    stored = {doc_id: kw for doc_id, kw in wired["doc_updates"]}
    assert set(stored) == {"d1", "d2"}
    d1 = json.loads(stored["d1"]["extracted_buckets"])
    assert d1["tables"]["invoices"]["rows"][0]["__chunk_index"] == "d1:0"
    assert d1["tables"]["invoices"]["chunk_pages"] == {"d1:0": [1]}
    assert wired["rebuilds"] == [["job-1"]]


def test_compatible_append_auto_rebuilds(wired):
    results = [_chunk_result("d9", "d9:0", [{"invoice_number": "5"}])]
    merge_task.merge_results.run(results, "job-1", "d9")

    reports = [
        json.loads(kw["compat_report"])
        for doc_id, kw in wired["doc_updates"]
        if "compat_report" in kw
    ]
    assert reports[0]["needs_review"] is False
    assert wired["rebuilds"] == [["job-1"]]
    assert "AWAITING_APPEND_REVIEW" not in wired["published"]


def test_incompatible_append_pauses_for_review(wired):
    results = [_chunk_result("d9", "d9:0", [])]  # zero rows extracted
    merge_task.merge_results.run(results, "job-1", "d9")

    assert wired["rebuilds"] == []
    assert "AWAITING_APPEND_REVIEW" in wired["published"]
    assert any(
        kw.get("status") == "AWAITING_APPEND_REVIEW" for kw in wired["job_updates"]
    )
```

NOTE: with zero rows the chunk entry still arrives (empty `rows`), so `per_doc` contains `d9` with an empty bucket — `build_compat_report` sees `total_rows == 0` → review. Confirm `_run_compat_gate` is reached even when `per_doc.get(append_document_id)` has only empty buckets (it is — the gate handles `payload=None` too).

- [ ] **Step 5: Run, lint, commit**

```bash
uv run pytest tests/test_merge_task.py -v   # 3 PASS
uv run pytest -q
uv run ruff check app/worker/tasks/extract.py app/worker/tasks/merge.py app/core/config.py tests/test_merge_task.py
uv run ruff format app/worker/tasks/extract.py app/worker/tasks/merge.py app/core/config.py tests/test_merge_task.py
git add app/worker/tasks/extract.py app/worker/tasks/merge.py app/core/config.py tests/test_merge_task.py
git commit -m "feat: per-document extraction buckets with append compatibility gate"
```

---

### Task 11: Append, resolve, delete-document, and rebuild endpoints

**Files:**
- Create: `apps/api/app/api/v1/documents.py`
- Modify: `apps/api/app/api/v1/router.py`
- Modify: `apps/api/app/api/v1/jobs.py` (rebuild endpoint)
- Modify: `apps/api/app/schemas/job.py` (two request models)
- Test: `apps/api/tests/integration/test_documents_api.py`

- [ ] **Step 1: Add request schemas**

In `apps/api/app/schemas/job.py`, after `DocumentResponse` (also add `from typing import Literal` to the imports):

```python
class DocumentCreateRequest(JobFileSpec):
    """Request body for appending a file to a completed dataset."""


class ResolveAppendRequest(BaseModel):
    """Force/cancel decision for an append that paused at the compat gate."""

    action: Literal["force", "cancel"]
```

- [ ] **Step 2: Create the documents router**

```python
# apps/api/app/api/v1/documents.py
"""ParseGrid API — Document endpoints (Dataset Consolidation).

Append a file to a completed dataset, resolve a paused append, delete a
document (with rebuild). All routes are user-scoped through the parent job.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.security import TokenPayload
from app.core.storage import delete_object_from_s3, delete_prefix_from_s3
from app.models.job import Document, DocumentStatus, Job, JobStatus, JobType
from app.schemas.job import DocumentCreateRequest, DocumentResponse, ResolveAppendRequest

router = APIRouter(prefix="/jobs/{job_id}/documents", tags=["Documents"])


async def _owned_job(job_id: str, user: TokenPayload, db: AsyncSession) -> Job:
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _document_on(job: Job, document_id: str) -> Document:
    for document in job.documents:
        if document.id == document_id:
            return document
    raise HTTPException(status_code=404, detail="Document not found")


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Append a file to a completed dataset",
)
async def append_document(
    job_id: str,
    body: DocumentCreateRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Document:
    job = await _owned_job(job_id, user, db)
    if job.job_type == JobType.TARGETED:
        raise HTTPException(status_code=400, detail="TARGETED jobs cannot be appended to")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job must be COMPLETED to append (currently {job.status})",
        )

    document = Document(
        id=str(uuid.uuid4()),
        job_id=job.id,
        filename=body.filename,
        file_key=body.file_key,
        file_size=body.file_size,
    )
    db.add(document)
    job.status = JobStatus.APPENDING
    job.progress = 0.0
    await db.commit()
    await db.refresh(document)

    from app.worker.tasks.ocr import process_document

    process_document.apply_async(args=[job.id, document.id], kwargs={"append": True})

    return document


@router.post(
    "/{document_id}/resolve",
    response_model=DocumentResponse,
    summary="Force or cancel an append paused at the compatibility gate",
)
async def resolve_append(
    job_id: str,
    document_id: str,
    body: ResolveAppendRequest,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Document:
    job = await _owned_job(job_id, user, db)
    if job.status != JobStatus.AWAITING_APPEND_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"No append awaiting review (job is {job.status})",
        )
    document = _document_on(job, document_id)
    if not (document.compat_report or {}).get("needs_review"):
        raise HTTPException(
            status_code=400, detail="This document is not awaiting review"
        )

    if body.action == "cancel":
        document.status = DocumentStatus.REJECTED
        job.status = JobStatus.COMPLETED
        job.progress = 100.0
        await db.commit()
        await db.refresh(document)
        return document

    # force: proceed with what mapped — rebuild from all EXTRACTED documents.
    job.status = JobStatus.RECONCILING
    job.progress = 0.0
    await db.commit()
    await db.refresh(document)

    from app.worker.tasks.reconcile import rebuild_dataset

    rebuild_dataset.apply_async(args=[job.id])

    return document


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a document from a dataset and rebuild the output",
)
async def delete_document(
    job_id: str,
    document_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    job = await _owned_job(job_id, user, db)
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job must be COMPLETED to remove documents (currently {job.status})",
        )
    document = _document_on(job, document_id)
    contributing = [
        d for d in job.documents if d.status == DocumentStatus.EXTRACTED
    ]
    needs_rebuild = document.status == DocumentStatus.EXTRACTED
    if needs_rebuild and len(contributing) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove the last contributing document — delete the job instead",
        )

    upload_prefix = (
        f"{document.file_key.rsplit('/', 1)[0]}/" if "/" in document.file_key else None
    )
    if upload_prefix:
        delete_prefix_from_s3(upload_prefix)
    else:
        delete_object_from_s3(document.file_key)
    delete_prefix_from_s3(f"parsed/{job_id}/{document_id}/")

    await db.delete(document)
    if needs_rebuild:
        job.status = JobStatus.RECONCILING
        job.progress = 0.0
    await db.commit()

    if needs_rebuild:
        from app.worker.tasks.reconcile import rebuild_dataset

        rebuild_dataset.apply_async(args=[job_id])
```

- [ ] **Step 3: Register the router and add the rebuild endpoint**

Read `apps/api/app/api/v1/router.py` and add (mirroring the existing includes):

```python
from app.api.v1.documents import router as documents_router
api_router.include_router(documents_router)
```

(Adapt names to the file's actual variable names.)

In `apps/api/app/api/v1/jobs.py`, add after `reject_model`:

```python
@router.post(
    "/{job_id}/rebuild",
    response_model=JobResponse,
    summary="Re-run reconcile → translate → provision from stored buckets",
)
async def rebuild_job(
    job_id: str,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Job:
    """Recovery path: rebuild the output database from document buckets."""
    query = select(Job).where(Job.id == job_id, Job.user_id == user.sub)
    result = await db.execute(query)
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=f"Job must be COMPLETED or FAILED to rebuild (currently {job.status})",
        )
    if not job.locked_model:
        raise HTTPException(status_code=400, detail="Job has no locked model")
    from app.models.job import DocumentStatus

    if not any(d.status == DocumentStatus.EXTRACTED for d in job.documents):
        raise HTTPException(status_code=400, detail="No extracted documents to rebuild from")

    job.status = JobStatus.RECONCILING
    job.progress = 0.0
    await db.commit()
    await db.refresh(job)

    from app.worker.tasks.reconcile import rebuild_dataset

    rebuild_dataset.apply_async(args=[job.id])

    return job
```

- [ ] **Step 4: Integration tests**

```python
# apps/api/tests/integration/test_documents_api.py
"""Tier 3: append/resolve/delete-document guards and scoping."""

import pytest

from tests.integration.conftest import auth_header
from tests.integration.test_jobs_api import PAYLOAD, _create

pytestmark = pytest.mark.integration

APPEND_BODY = {"filename": "feb.pdf", "file_key": "uploads/u/z/feb.pdf", "file_size": 50}


@pytest.fixture
def no_celery(monkeypatch):
    from app.worker.tasks import ocr as ocr_tasks
    from app.worker.tasks import reconcile as reconcile_tasks

    monkeypatch.setattr(ocr_tasks.process_document, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(reconcile_tasks.rebuild_dataset, "apply_async", lambda *a, **k: None)


async def _complete_job(client, db_exec, user="user-a"):
    """Create a job and force it COMPLETED directly in the DB."""
    job = await _create(client, user)
    await db_exec(
        "UPDATE jobs SET status = 'COMPLETED', progress = 100.0 WHERE id = :id",
        {"id": job["id"]},
    )
    await db_exec(
        "UPDATE documents SET status = 'EXTRACTED', "
        "extracted_buckets = '{\"tables\": {}}' WHERE job_id = :id",
        {"id": job["id"]},
    )
    return job


@pytest.fixture
async def db_exec():
    """Raw-SQL executor bound to the test database (same engine config as the app)."""
    import sqlalchemy
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.integration.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL)

    async def _exec(sql: str, params: dict):
        async with engine.begin() as conn:
            await conn.execute(sqlalchemy.text(sql), params)

    yield _exec
    await engine.dispose()


async def test_append_requires_completed(client, no_celery):
    job = await _create(client, "user-a")  # status UPLOADED
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-a"),
    )
    assert res.status_code == 409


async def test_append_rejects_targeted(client, no_celery, db_exec):
    res = await client.post(
        "/api/v1/jobs",
        json={**PAYLOAD, "job_type": "TARGETED"},
        headers=auth_header("user-a"),
    )
    job = res.json()
    await db_exec(
        "UPDATE jobs SET status = 'COMPLETED' WHERE id = :id", {"id": job["id"]}
    )
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-a"),
    )
    assert res.status_code == 400


async def test_append_happy_path_sets_appending(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-a"),
    )
    assert res.status_code == 201
    assert res.json()["status"] == "PENDING"
    refreshed = await client.get(f"/api/v1/jobs/{job['id']}", headers=auth_header("user-a"))
    assert refreshed.json()["status"] == "APPENDING"
    assert refreshed.json()["document_count"] == 2


async def test_append_is_user_scoped(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/documents",
        json=APPEND_BODY,
        headers=auth_header("user-b"),
    )
    assert res.status_code == 404


async def test_cannot_delete_last_contributing_document(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    doc_id = job["documents"][0]["id"]
    res = await client.delete(
        f"/api/v1/jobs/{job['id']}/documents/{doc_id}",
        headers=auth_header("user-a"),
    )
    assert res.status_code == 400


async def test_rebuild_requires_extracted_documents(client, no_celery, db_exec):
    job = await _complete_job(client, db_exec)
    res = await client.post(
        f"/api/v1/jobs/{job['id']}/rebuild", headers=auth_header("user-a")
    )
    # locked_model is null on this seeded job → 400
    assert res.status_code == 400
```

NOTE: `_complete_job` flips the seeded job's status with raw SQL because the real pipeline (Celery) is not running in Tier 3. `db_exec` deliberately does not depend on `client` — order the fixtures `client, no_celery, db_exec` in test signatures so the `client` fixture has created the tables before `db_exec` is used.

- [ ] **Step 5: Run, lint, commit**

```bash
uv run pytest tests/integration/test_documents_api.py -v   # 6 PASS
uv run pytest -q
uv run ruff check app/api/v1/documents.py app/api/v1/router.py app/api/v1/jobs.py app/schemas/job.py tests/integration/test_documents_api.py
uv run ruff format app/api/v1/documents.py app/api/v1/router.py app/api/v1/jobs.py app/schemas/job.py tests/integration/test_documents_api.py
git add app/api/v1/documents.py app/api/v1/router.py app/api/v1/jobs.py app/schemas/job.py tests/integration/test_documents_api.py
git commit -m "feat: append/resolve/delete-document and rebuild endpoints"
```

---

### Task 12: Drop legacy job file columns

**Files:**
- Create: `apps/api/alembic/versions/b2e1d6f8c302_drop_legacy_job_file_columns.py`
- Modify: `apps/api/app/models/job.py`, `apps/api/app/schemas/job.py`, `apps/api/app/api/v1/jobs.py`, `apps/api/app/worker/tasks/ocr.py`, `apps/api/tests/integration/test_worker_db_documents.py`

- [ ] **Step 1: Migration**

```python
"""drop legacy single-file columns from jobs

Revision ID: b2e1d6f8c302
Revises: a1d0c5e7b201
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e1d6f8c302"
down_revision: str | None = "a1d0c5e7b201"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("jobs", "filename")
    op.drop_column("jobs", "file_key")
    op.drop_column("jobs", "file_size")
    op.drop_column("jobs", "page_count")


def downgrade() -> None:
    op.add_column("jobs", sa.Column("filename", sa.String(length=512), nullable=True))
    op.add_column("jobs", sa.Column("file_key", sa.String(length=1024), nullable=True))
    op.add_column(
        "jobs", sa.Column("file_size", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("jobs", sa.Column("page_count", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE jobs j SET
            filename = d.filename,
            file_key = d.file_key,
            file_size = d.file_size,
            page_count = d.page_count
        FROM (
            SELECT DISTINCT ON (job_id) job_id, filename, file_key, file_size, page_count
            FROM documents ORDER BY job_id, created_at
        ) d
        WHERE d.job_id = j.id
        """
    )
```

- [ ] **Step 2: Remove every code reference to the dropped columns**

1. `app/models/job.py`: delete the `filename`, `file_key`, `file_size`, `page_count` mapped columns from `Job`.
2. `app/schemas/job.py` `JobResponse`: delete the `filename: str`, `file_key: str`, `file_size: int`, and `page_count: int | None` fields (the `documents` list + computed fields replace them).
3. `app/api/v1/jobs.py` `create_job`: remove the three legacy kwargs (`filename=first.filename, file_key=first.file_key, file_size=first.file_size`) and the `first = body.files[0]` line.
4. `app/worker/tasks/ocr.py` `ocr_complete`: remove `page_count=total_pages` from the `update_job(...)` call (keep the `total_pages` computation only if still used in a log line; otherwise delete it).
5. `tests/integration/test_worker_db_documents.py`: remove `filename=`, `file_key=`, `file_size=` kwargs from the seeded `Job(...)`.
6. Run `grep -rn "job.filename\|job\.file_key\|job\.file_size\|job\.page_count" app/ tests/` and `grep -rn '"filename"' app/worker/` — fix any remaining reads of the dropped Job columns (Document reads stay). The SSE initial snapshot and `JobStatusResponse` never used them — verify with the grep.

- [ ] **Step 3: Apply, verify, commit**

```bash
uv run alembic upgrade head
uv run pytest -q          # all green (incl. migration test, which now covers both revisions)
uv run ruff check app/ tests/ && uv run ruff format app/ tests/
git add -u apps/api && git add alembic/versions/b2e1d6f8c302_drop_legacy_job_file_columns.py
git commit -m "feat!: jobs are datasets — drop legacy single-file columns"
```

(Use `git add -u` from `apps/api/`; never `git add .` — `scripts/` must stay unstaged.)

---

### Task 13: Frontend API client and hooks

**Files:**
- Modify: `apps/web/src/lib/api-client.ts`
- Modify: `apps/web/src/hooks/use-jobs.ts`

- [ ] **Step 1: Types** — in `api-client.ts`, add after the `DocumentProfile` interface:

```ts
export type DocumentStatus =
  | "PENDING"
  | "OCR_PROCESSING"
  | "OCR_DONE"
  | "EXTRACTING"
  | "EXTRACTED"
  | "FAILED"
  | "REJECTED";

export interface CompatReport {
  total_rows: number;
  rows_per_table: Record<string, number>;
  pk_null_rows: number;
  pk_null_ratio: number;
  pk_tables: string[];
  empty_pk_tables: string[];
  profile_drift: Record<string, Record<string, number>> | null;
  reasons: string[];
  needs_review: boolean;
}

export interface JobDocument {
  id: string;
  job_id: string;
  filename: string;
  file_key: string;
  file_size: number;
  page_count: number | null;
  status: DocumentStatus;
  compat_report: CompatReport | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}
```

In the `Job` interface: remove `filename`, `file_key`, `file_size`, `page_count`; add:

```ts
  documents: JobDocument[];
  document_count: number;
  total_pages: number | null;
  total_file_size: number;
```

- [ ] **Step 2: Methods** — replace `createJob` and add document methods:

```ts
  createJob: (
    data: {
      files: { filename: string; file_key: string; file_size: number }[];
      output_format?: string;
      job_type?: JobType;
    },
    token: string,
  ) =>
    request<Job>("/api/v1/jobs", {
      method: "POST",
      body: data,
      token,
    }),

  appendDocument: (
    jobId: string,
    data: { filename: string; file_key: string; file_size: number },
    token: string,
  ) =>
    request<JobDocument>(`/api/v1/jobs/${jobId}/documents`, {
      method: "POST",
      body: data,
      token,
    }),

  resolveAppend: (
    jobId: string,
    documentId: string,
    action: "force" | "cancel",
    token: string,
  ) =>
    request<JobDocument>(`/api/v1/jobs/${jobId}/documents/${documentId}/resolve`, {
      method: "POST",
      body: { action },
      token,
    }),

  deleteDocument: (jobId: string, documentId: string, token: string) =>
    request<void>(`/api/v1/jobs/${jobId}/documents/${documentId}`, {
      method: "DELETE",
      token,
    }),

  rebuildJob: (jobId: string, token: string) =>
    request<Job>(`/api/v1/jobs/${jobId}/rebuild`, {
      method: "POST",
      token,
    }),
```

- [ ] **Step 3: Hooks** — in `use-jobs.ts`:

Add `"AWAITING_APPEND_REVIEW"` to `IDLE_STATUSES` (APPENDING stays active/polling).

Update `useCreateJob`'s mutation input type to the new shape:

```ts
    mutationFn: (data: {
      files: {filename: string; file_key: string; file_size: number}[];
      output_format?: string;
      job_type?: "FULL" | "TARGETED";
    }) => api.createJob(data, token),
```

Append new hooks:

```ts
export function useAppendDocument(token: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      jobId,
      file,
    }: {
      jobId: string;
      file: {filename: string; file_key: string; file_size: number};
    }) => api.appendDocument(jobId, file, token),
    onSuccess: (data) => {
      queryClient.invalidateQueries({queryKey: ["job", data.job_id]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}

export function useResolveAppend(token: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      jobId,
      documentId,
      action,
    }: {
      jobId: string;
      documentId: string;
      action: "force" | "cancel";
    }) => api.resolveAppend(jobId, documentId, action, token),
    onSuccess: (data) => {
      queryClient.invalidateQueries({queryKey: ["job", data.job_id]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}

export function useDeleteDocument(token: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({jobId, documentId}: {jobId: string; documentId: string}) =>
      api.deleteDocument(jobId, documentId, token),
    onSuccess: (_, {jobId}) => {
      queryClient.invalidateQueries({queryKey: ["job", jobId]});
      queryClient.invalidateQueries({queryKey: ["jobs"]});
    },
  });
}
```

- [ ] **Step 4: Build will FAIL here — that is expected.** `pnpm --dir apps/web build` now errors in `dashboard/client.tsx`, `jobs/new/client.tsx`, and `jobs/[id]/client.tsx` (they still read `job.filename` and call the old `createJob`). Tasks 14–15 fix the call sites. Do NOT commit yet — Tasks 13+14+15 land as a coherent series; commit this task's files only after confirming the typecheck failures are exactly the expected call-site ones:

```bash
pnpm --dir apps/web build 2>&1 | grep "error" | head -20   # only filename/createJob call-site errors
git add apps/web/src/lib/api-client.ts apps/web/src/hooks/use-jobs.ts
git commit -m "feat(web): dataset-aware API client types and document hooks"
```

---

### Task 14: Frontend — multi-file new-job page

**Files:**
- Modify: `apps/web/src/components/upload/dropzone.tsx`
- Modify: `apps/web/src/app/jobs/new/client.tsx`

- [ ] **Step 1: Dropzone multi-select**

In `dropzone.tsx`, extend the props and selection handling (keep all styling):

```ts
interface DropzoneProps {
  onFilesSelected: (files: File[]) => void;
  isUploading?: boolean;
  accept?: string;
  maxSizeMB?: number;
  multiple?: boolean;
}
```

Rename the single-file callback usage: `validateAndSelect` becomes:

```ts
  const validateAndSelect = useCallback(
    (files: File[]) => {
      setError(null);
      const allowedExts = accept.split(",").map((s) => s.trim().replace(".", ""));
      const valid: File[] = [];
      for (const file of files) {
        if (file.size > maxSizeMB * 1024 * 1024) {
          setError(`${file.name}: too large. Maximum size: ${maxSizeMB}MB`);
          continue;
        }
        const ext = file.name.split(".").pop()?.toLowerCase();
        if (ext && !allowedExts.includes(ext)) {
          setError(`${file.name}: unsupported format. Allowed: ${accept}`);
          continue;
        }
        valid.push(file);
      }
      if (valid.length) onFilesSelected(multiple ? valid : valid.slice(0, 1));
    },
    [onFilesSelected, maxSizeMB, accept, multiple],
  );
```

Update `handleDrop` to pass all files: `validateAndSelect(Array.from(e.dataTransfer.files));` and the `<input type="file" ...>` element to include `multiple={multiple}` with its onChange passing `Array.from(e.target.files ?? [])`. Read the rest of the file and adjust the remaining references (`onFileSelected` no longer exists — this is a breaking prop rename; the new-job page is its only consumer, updated next).

- [ ] **Step 2: New-job client**

In `apps/web/src/app/jobs/new/client.tsx`:

- State: `const [selectedFiles, setSelectedFiles] = useState<File[]>([]);`
- Handler:

```ts
  const handleFilesSelected = (files: File[]) => {
    setSelectedFiles((prev) =>
      jobType === "TARGETED" ? files.slice(0, 1) : [...prev, ...files]
    );
    setError(null);
  };

  const removeFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  };
```

- When the user switches to TARGETED, trim to one file: in the TARGETED button onClick: `setJobType("TARGETED"); setSelectedFiles((prev) => prev.slice(0, 1));`
- Submit handler:

```ts
  const handleSubmit = async () => {
    if (!selectedFiles.length || !token) return;
    setIsUploading(true);
    setError(null);
    try {
      const files: {filename: string; file_key: string; file_size: number}[] = [];
      for (const file of selectedFiles) {
        const formData = new FormData();
        formData.append("file", file);
        const uploadRes = await fetch(`${API_BASE}/api/v1/upload/direct`, {
          method: "POST",
          headers: {Authorization: `Bearer ${token}`},
          body: formData,
        });
        if (!uploadRes.ok) throw new Error(`Upload failed: ${file.name}`);
        const {file_key} = await uploadRes.json();
        files.push({filename: file.name, file_key, file_size: file.size});
      }

      const jobRes = await fetch(`${API_BASE}/api/v1/jobs`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          files,
          output_format: outputFormat,
          job_type: jobType,
        }),
      });
      if (!jobRes.ok) throw new Error("Job creation failed");
      const job = await jobRes.json();
      router.push(`/jobs/${job.id}`);
    } catch (e) {
      setError((e as Error).message);
      setIsUploading(false);
    }
  };
```

- Render: `<Dropzone onFilesSelected={handleFilesSelected} isUploading={isUploading} multiple={jobType === "FULL"} />`; replace the single `selectedFile` card with a list (map over `selectedFiles`, same card styling, the remove button calls `removeFile(index)`); the submit button's disabled condition becomes `!selectedFiles.length || isUploading || !token` and its label `Start Extraction` / `Processing…` unchanged. Under TARGETED mode add a hint line under the mode picker: `<p className='text-xs text-zinc-600'>Targeted mode processes a single document.</p>` (only when `jobType === "TARGETED"`).

- [ ] **Step 3: Verify and commit**

```bash
cd /Users/pragadeesh/Developer/parsegrid && pnpm --dir apps/web build 2>&1 | grep -E "error|Compiled"
# Expected: remaining errors ONLY in dashboard/client.tsx and jobs/[id]/client.tsx (Task 15)
git add apps/web/src/components/upload/dropzone.tsx apps/web/src/app/jobs/new/client.tsx
git commit -m "feat(web): multi-file upload on the new-job page"
```

---

### Task 15: Frontend — job detail documents, append flow, dashboard

**Files:**
- Modify: `apps/web/src/app/jobs/[id]/client.tsx`
- Modify: `apps/web/src/app/dashboard/client.tsx`
- Modify: `apps/web/src/components/job-status/status-badge.tsx`
- Create: `apps/web/src/components/documents/documents-card.tsx`
- Create: `apps/web/src/components/documents/append-review.tsx`

- [ ] **Step 1: Status badge entries** — in `status-badge.tsx` add to `BADGE_STYLES`:

```ts
  APPENDING: EMERALD,
  AWAITING_APPEND_REVIEW: AMBER,
```

and to `STATUS_LABELS`:

```ts
  APPENDING: "Adding Data",
  AWAITING_APPEND_REVIEW: "Review Append",
```

(`progress-bar.tsx` falls back to its UPLOADED config for unknown statuses — read its `STATUS_CONFIG` and add `APPENDING` / `AWAITING_APPEND_REVIEW` entries following the exact shape of the existing entries, with labels "Adding Data" / "Review Append" and the same stage index as `RECONCILING` if the config uses stages; mirror whatever fields the existing entries carry.)

- [ ] **Step 2: Documents card component**

```tsx
// apps/web/src/components/documents/documents-card.tsx
/**
 * ParseGrid — Dataset documents list with per-file status and append entry.
 */

"use client";

import {useRef, useState} from "react";
import {TrashIcon} from "@phosphor-icons/react/dist/ssr/Trash";
import type {JobDocument} from "@/lib/api-client";
import {StatusBadge} from "@/components/job-status/status-badge";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface DocumentsCardProps {
  documents: JobDocument[];
  canAppend: boolean;
  canDelete: boolean;
  token: string | null;
  onAppend: (file: {filename: string; file_key: string; file_size: number}) => void;
  onDelete: (documentId: string) => void;
  isAppending: boolean;
}

export function DocumentsCard({
  documents,
  canAppend,
  canDelete,
  token,
  onAppend,
  onDelete,
  isAppending,
}: DocumentsCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleFile = async (file: File) => {
    if (!token) return;
    setIsUploading(true);
    setUploadError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${API_BASE}/api/v1/upload/direct`, {
        method: "POST",
        headers: {Authorization: `Bearer ${token}`},
        body: formData,
      });
      if (!res.ok) throw new Error(`Upload failed: ${file.name}`);
      const {file_key} = await res.json();
      onAppend({filename: file.name, file_key, file_size: file.size});
    } catch (e) {
      setUploadError((e as Error).message);
    } finally {
      setIsUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div className='rounded-2xl border border-zinc-800/60 bg-zinc-900/30 p-6'>
      <div className='flex items-center justify-between'>
        <h3 className='text-xs font-semibold uppercase tracking-wider text-zinc-500'>
          Documents ({documents.length})
        </h3>
        {canAppend && (
          <>
            <input
              ref={inputRef}
              type='file'
              accept='.pdf,.png,.jpg,.jpeg,.tiff,.bmp'
              className='hidden'
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
              }}
            />
            <button
              onClick={() => inputRef.current?.click()}
              disabled={isUploading || isAppending}
              className='rounded-xl border border-emerald-600/40 bg-emerald-600/10 px-4 py-2 text-sm font-medium text-emerald-400 transition-all hover:bg-emerald-600/20 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50'>
              {isUploading || isAppending ? "Adding…" : "Add data"}
            </button>
          </>
        )}
      </div>
      {uploadError && (
        <div className='mt-3 rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-2 text-sm text-red-400'>
          {uploadError}
        </div>
      )}
      <ul className='mt-4 divide-y divide-zinc-800/60'>
        {documents.map((doc) => (
          <li key={doc.id} className='flex items-center justify-between gap-3 py-3'>
            <div className='min-w-0'>
              <p className='truncate text-sm font-medium text-zinc-200'>{doc.filename}</p>
              <p className='text-xs font-mono text-zinc-500'>
                {(doc.file_size / 1024 / 1024).toFixed(2)} MB
                {doc.page_count ? ` · ${doc.page_count} pages` : ""}
              </p>
            </div>
            <div className='flex items-center gap-2'>
              <StatusBadge status={doc.status} />
              {canDelete && documents.length > 1 && (
                <button
                  onClick={() => onDelete(doc.id)}
                  aria-label={`Remove ${doc.filename}`}
                  className='rounded-lg p-1.5 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-red-300'>
                  <TrashIcon className='h-4 w-4' />
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

NOTE: `StatusBadge` is reused for document statuses — add these entries to `BADGE_STYLES`/`STATUS_LABELS` in Step 1 as well: `PENDING: NEUTRAL`, `OCR_DONE: NEUTRAL_LIGHT`, `EXTRACTED: EMERALD`, `REJECTED: NEUTRAL` and labels `PENDING: "Pending"`, `OCR_DONE: "OCR Done"`, `EXTRACTED: "Extracted"`, `REJECTED: "Rejected"` (OCR_PROCESSING/EXTRACTING/FAILED already exist).

- [ ] **Step 3: Append review component**

```tsx
// apps/web/src/components/documents/append-review.tsx
/**
 * ParseGrid — Force/cancel review for an append that failed the compat gate.
 */

"use client";

import type {JobDocument} from "@/lib/api-client";

interface AppendReviewProps {
  document: JobDocument;
  onResolve: (action: "force" | "cancel") => void;
  isSubmitting: boolean;
}

export function AppendReview({document, onResolve, isSubmitting}: AppendReviewProps) {
  const report = document.compat_report;
  if (!report) return null;

  return (
    <div className='rounded-2xl border border-amber-500/20 bg-amber-500/5 p-6'>
      <h3 className='text-sm font-semibold text-amber-300'>
        “{document.filename}” doesn’t fit this dataset cleanly
      </h3>
      <ul className='mt-2 list-disc pl-5 text-sm text-zinc-400'>
        {report.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
      <div className='mt-4 grid grid-cols-2 gap-4 text-sm sm:grid-cols-4'>
        <div>
          <dt className='text-zinc-500'>Rows extracted</dt>
          <dd className='mt-1 font-mono text-zinc-200'>{report.total_rows}</dd>
        </div>
        {Object.entries(report.rows_per_table).map(([table, count]) => (
          <div key={table}>
            <dt className='truncate text-zinc-500'>{table}</dt>
            <dd className='mt-1 font-mono text-zinc-200'>{count}</dd>
          </div>
        ))}
      </div>
      <div className='mt-5 flex gap-3'>
        <button
          onClick={() => onResolve("force")}
          disabled={isSubmitting}
          className='rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-medium text-white transition-all hover:bg-emerald-500 active:scale-[0.98] disabled:opacity-50'>
          Add what fits
        </button>
        <button
          onClick={() => onResolve("cancel")}
          disabled={isSubmitting}
          className='rounded-xl border border-zinc-700 px-5 py-2.5 text-sm font-medium text-zinc-300 transition-all hover:border-zinc-600 active:scale-[0.98] disabled:opacity-50'>
          Discard this file
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire the job detail page**

In `apps/web/src/app/jobs/[id]/client.tsx`:

1. Imports: add `DocumentsCard`, `AppendReview`, `useAppendDocument`, `useResolveAppend`, `useDeleteDocument`.
2. Hooks: `const appendMutation = useAppendDocument(token ?? ""); const resolveMutation = useResolveAppend(token ?? ""); const deleteDocMutation = useDeleteDocument(token ?? "");`
3. `isProcessing`: add `&& job.status !== "AWAITING_APPEND_REVIEW"` to the chain (APPENDING stays processing → SSE on).
4. SSE terminal invalidation list: add `data.status === "AWAITING_APPEND_REVIEW"` to the invalidate condition.
5. Display name: define `const datasetName = job.documents[0]?.filename ?? "Dataset";` and replace both `{job.filename}` renders (breadcrumb + h1) with `{datasetName}`; replace `aria-label={`Delete ${job.filename}`}` and the ConfirmDialog description's `"${job.filename}"` with `datasetName`. When `job.document_count > 1`, render after the h1: `<span className='ml-2 text-xs font-mono text-zinc-500'>+{job.document_count - 1} more file{job.document_count > 2 ? "s" : ""}</span>`.
6. File-size metadata cell: `{(job.total_file_size / 1024 / 1024).toFixed(2)} MB`; pages cell: `{job.total_pages && (...{job.total_pages}...)}`.
7. After the Progress card, render the review panel and documents card:

```tsx
          {/* Append review (compat gate) */}
          {job.status === "AWAITING_APPEND_REVIEW" &&
            (() => {
              const pending = [...job.documents]
                .reverse()
                .find((d) => d.compat_report?.needs_review);
              return pending ? (
                <AppendReview
                  document={pending}
                  isSubmitting={resolveMutation.isPending}
                  onResolve={(action) =>
                    resolveMutation.mutate({jobId, documentId: pending.id, action})
                  }
                />
              ) : null;
            })()}

          {/* Documents */}
          <DocumentsCard
            documents={job.documents}
            canAppend={job.status === "COMPLETED" && job.job_type === "FULL"}
            canDelete={job.status === "COMPLETED"}
            token={token}
            isAppending={appendMutation.isPending}
            onAppend={(file) => appendMutation.mutate({jobId, file})}
            onDelete={(documentId) => deleteDocMutation.mutate({jobId, documentId})}
          />
```

- [ ] **Step 5: Dashboard**

In `apps/web/src/app/dashboard/client.tsx`, the job rows read `job.filename` — replace each with `job.documents[0]?.filename ?? "Dataset"` (the delete-dialog helper takes the same expression; keep its local `filename` parameter name). Where the row shows the name, append a count chip when multi-file:

```tsx
{job.document_count > 1 && (
  <span className='ml-1.5 text-xs font-mono text-zinc-500'>+{job.document_count - 1}</span>
)}
```

(Read the file first; there is also a local `interface`/type near line 31 declaring `filename: string` for the delete candidate — that stays, it's a UI-local type.)

- [ ] **Step 6: Build green, commit**

```bash
cd /Users/pragadeesh/Developer/parsegrid && pnpm --dir apps/web build
# Expected: ✓ Compiled successfully
git add apps/web/src
git commit -m "feat(web): dataset documents UI — append, review gate, rebuild-aware detail page"
```

---

### Task 16: Spec coverage sweep — Tier 1 gaps

**Files:**
- Test: append to `apps/api/tests/test_consolidation.py`

- [ ] **Step 1: Cross-document dedupe and provenance keys at the reconciliation boundary** (append to the file; these exercise `union_buckets → reconcile_model` together, the integration the spec calls out):

```python
class TestUnionThroughReconciliation:
    def test_same_entity_across_documents_dedupes_to_one_row(self):
        from app.services.reconciliation import reconcile_model

        doc_a = {
            "tables": {
                "invoices": {
                    "rows": [
                        {"invoice_number": "INV-1", "note": None, "__chunk_index": "a:0"}
                    ],
                    "chunk_pages": {"a:0": [1]},
                }
            }
        }
        doc_b = {
            "tables": {
                "invoices": {
                    "rows": [
                        {"invoice_number": "inv-1 ", "note": "paid", "__chunk_index": "b:0"}
                    ],
                    "chunk_pages": {"b:0": [3]},
                }
            }
        }
        rows, pages = union_buckets([("a", doc_a), ("b", doc_b)])
        finalized, _ = reconcile_model(
            bucketed_rows=rows, chunk_pages_by_index=pages, locked_model=MODEL
        )
        assert len(finalized["invoices"]) == 1
        merged = finalized["invoices"][0]
        assert merged["note"] == "paid"  # null filled from the other document

    def test_provenance_pages_survive_document_prefixed_keys(self):
        from app.services.reconciliation import reconcile_model

        doc = {
            "tables": {
                "invoices": {
                    "rows": [
                        {"invoice_number": "X", "note": "n", "__chunk_index": "docZ:2"}
                    ],
                    "chunk_pages": {"docZ:2": [7, 8]},
                }
            }
        }
        rows, pages = union_buckets([("docZ", doc)])
        finalized, _ = reconcile_model(
            bucketed_rows=rows, chunk_pages_by_index=pages, locked_model=MODEL
        )
        assert finalized["invoices"][0]["source_page_numbers"] == [7, 8]
```

- [ ] **Step 2: Run, lint, commit**

```bash
uv run pytest tests/test_consolidation.py -v   # 13 PASS
uv run pytest -q
uv run ruff check tests/test_consolidation.py && uv run ruff format tests/test_consolidation.py
git add tests/test_consolidation.py
git commit -m "test: cross-document dedupe and provenance through reconciliation"
```

---

### Task 17: Final verification, spec close-out, push

- [ ] **Step 1: Full local verification**

```bash
cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
cd /Users/pragadeesh/Developer/parsegrid && pnpm --dir apps/web build
```

Expected: lint clean, full suite green (unit + Tier 2 + Tier 3 with Postgres up), web build compiles.

- [ ] **Step 2: Update the spec status**

In `docs/superpowers/specs/2026-06-11-dataset-consolidation-design.md`, change `**Status:** Approved` to `**Status:** Implemented`.

- [ ] **Step 3: Commit, push, watch CI**

```bash
git add docs/superpowers/specs/2026-06-11-dataset-consolidation-design.md
git commit -m "docs: mark Dataset Consolidation spec implemented"
git push origin main
gh run list --limit 1   # grab the run id
gh run watch <id> --exit-status
```

Expected: api, web green (audit informational). Fix CI-only issues if any, then done — next sub-project is Export & Download (own brainstorm + spec).
