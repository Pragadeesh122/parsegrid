"""Add pgvector extension, job_type enum, INDEXING/AWAITING_QUERY statuses, and document_chunks table.

Revision ID: d89bf08bddb0
Revises: 14f28221067a
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "d89bf08bddb0"
down_revision = "14f28221067a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Add new values to job_status enum
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'INDEXING' AFTER 'OCR_PROCESSING'")
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'AWAITING_QUERY' AFTER 'INDEXING'")

    # 3. Create job_type enum and add column
    job_type_enum = sa.Enum("FULL", "TARGETED", name="job_type", create_constraint=True)
    job_type_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "jobs",
        sa.Column("job_type", job_type_enum, nullable=False, server_default="FULL"),
    )
    op.add_column(
        "jobs",
        sa.Column("target_chunks", sa.JSON(), nullable=True),
    )

    # 4. Create document_chunks table (without embedding — added via raw SQL for vector type)
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
    )

    # Add vector column via raw SQL (Alembic doesn't natively support pgvector types)
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(3072)")


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_column("jobs", "target_chunks")
    op.drop_column("jobs", "job_type")
    op.execute("DROP TYPE IF EXISTS job_type")
    # Note: Cannot remove enum values from job_status in PostgreSQL
