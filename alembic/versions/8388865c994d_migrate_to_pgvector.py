"""migrate to pgvector

Revision ID: 8388865c994d
Revises: 39095ba57274
Create Date: 2026-03-08 16:58:03.543106

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8388865c994d"
down_revision: Union[str, Sequence[str], None] = "39095ba57274"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: enable pgvector, convert embedding column to vector(1536)."""
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Truncate existing BYTEA data (will be regenerated)
    op.execute("TRUNCATE TABLE catalog_embeddings")

    # Change column type from BYTEA to vector(1536) using NULL for existing rows
    op.execute(
        "ALTER TABLE catalog_embeddings ALTER COLUMN embedding TYPE vector(1536) USING NULL"
    )

    # Add HNSW index for cosine similarity search
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_catalog_embeddings_embedding_hnsw
        ON catalog_embeddings
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    """Downgrade schema: remove HNSW index, convert embedding back to BYTEA."""
    op.execute("DROP INDEX IF EXISTS ix_catalog_embeddings_embedding_hnsw")
    op.execute(
        "ALTER TABLE catalog_embeddings ALTER COLUMN embedding TYPE BYTEA USING NULL"
    )
    op.execute("TRUNCATE TABLE catalog_embeddings")
