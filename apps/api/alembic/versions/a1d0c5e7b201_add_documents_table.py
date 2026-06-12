"""add documents table, backfill from jobs, extend job_status enum

Revision ID: a1d0c5e7b201
Revises: 7a1c4e9b2d31
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

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
            postgresql.ENUM(name="document_status", create_type=False),
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
