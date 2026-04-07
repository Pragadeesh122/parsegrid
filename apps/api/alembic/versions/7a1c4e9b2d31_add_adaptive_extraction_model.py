"""add adaptive extraction model columns and statuses (Phase 7)

Revision ID: 7a1c4e9b2d31
Revises: 5d8f2c6f0b31
Create Date: 2026-04-06

Phase 7 swaps the flat schema columns for an adaptive `DatabaseModel`:

* New job_status values: PROFILING, MODEL_PROPOSED, MODEL_LOCKED, RECONCILING.
* Old SCHEMA_PROPOSED / SCHEMA_LOCKED rows are transitioned to FAILED with a
  migration note. The enum values themselves cannot be removed from a Postgres
  enum without recreating the type, so they are intentionally left as dead
  values — no row will reference them after this migration.
* Drops jobs.proposed_schema and jobs.locked_schema.
* Adds jobs.document_profile, jobs.proposed_model, jobs.locked_model,
  jobs.section_map (all JSON, nullable).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a1c4e9b2d31"
down_revision: Union[str, None] = "5d8f2c6f0b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Extend job_status enum with the four new values.
    #    ADD VALUE IF NOT EXISTS works inside Alembic's transaction on PG 12+.
    op.execute(
        "ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'PROFILING' AFTER 'AWAITING_QUERY'"
    )
    op.execute(
        "ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'MODEL_PROPOSED' AFTER 'PROFILING'"
    )
    op.execute(
        "ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'MODEL_LOCKED' AFTER 'AWAITING_REVIEW'"
    )
    op.execute(
        "ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'RECONCILING' AFTER 'MERGING'"
    )

    # 2. Transition any rows referencing the removed statuses to FAILED so the
    #    drop of proposed_schema / locked_schema does not orphan in-flight jobs.
    op.execute(
        """
        UPDATE jobs
        SET status = 'FAILED',
            error_message = COALESCE(error_message, '') ||
                            'Phase 7 migration: pipeline reset. Please re-upload.',
            updated_at = NOW()
        WHERE status IN ('SCHEMA_PROPOSED', 'SCHEMA_LOCKED')
        """
    )

    # 3. Drop the legacy schema columns.
    op.drop_column("jobs", "proposed_schema")
    op.drop_column("jobs", "locked_schema")

    # 4. Add the adaptive model columns.
    op.add_column(
        "jobs",
        sa.Column(
            "document_profile",
            sa.JSON(),
            nullable=True,
            comment=(
                "Whole-document profile (Phase 7): sampled pages, region histogram, "
                "sections."
            ),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "proposed_model",
            sa.JSON(),
            nullable=True,
            comment="AI-proposed DatabaseModel (Phase 7 discovery).",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "locked_model",
            sa.JSON(),
            nullable=True,
            comment="User-approved DatabaseModel after review (Phase 7).",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "section_map",
            sa.JSON(),
            nullable=True,
            comment=(
                "Optional list[SectionCandidate] for UI grouping and table-routed "
                "extraction."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "section_map")
    op.drop_column("jobs", "locked_model")
    op.drop_column("jobs", "proposed_model")
    op.drop_column("jobs", "document_profile")

    op.add_column(
        "jobs",
        sa.Column("locked_schema", sa.JSON(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("proposed_schema", sa.JSON(), nullable=True),
    )
    # Note: Cannot remove enum values from job_status in PostgreSQL without
    # recreating the type. PROFILING/MODEL_PROPOSED/MODEL_LOCKED/RECONCILING
    # remain in the enum after downgrade.
