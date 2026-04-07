"""add HNSW index for document chunk embeddings

Revision ID: 5d8f2c6f0b31
Revises: d89bf08bddb0
Create Date: 2026-04-05 12:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d8f2c6f0b31"
down_revision: Union[str, None] = "d89bf08bddb0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The embedding column is `vector(3072)` (text-embedding-3-large), which is
    # above pgvector's 2000-dim cap for HNSW on the full-precision `vector` op
    # class. We index the half-precision cast instead — pgvector 0.7+ supports
    # `halfvec` HNSW up to 4000 dims and the recall loss vs. fp32 is below the
    # noise floor of OpenAI embedding similarity. Queries must cast to
    # `halfvec(3072)` in the ORDER BY for the planner to use this index.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx
        ON document_chunks
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_hnsw_idx")
