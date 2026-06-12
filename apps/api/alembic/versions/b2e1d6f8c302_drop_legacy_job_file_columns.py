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
