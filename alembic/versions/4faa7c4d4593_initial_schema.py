"""initial schema

Revision ID: 4faa7c4d4593
Revises:
Create Date: 2026-03-08 14:50:38.735814

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4faa7c4d4593"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_embeddings (
            id UUID NOT NULL,
            item_number VARCHAR NOT NULL,
            description VARCHAR NOT NULL,
            provider VARCHAR NOT NULL,
            embedding BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_catalog_embeddings_item_number
        ON catalog_embeddings (item_number)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingredient_cache (
            id UUID NOT NULL,
            ingredient_name VARCHAR NOT NULL,
            sysco_item_number VARCHAR,
            source VARCHAR NOT NULL,
            provider VARCHAR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_ingredient_cache_ingredient_name
        ON ingredient_cache (ingredient_name)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id UUID NOT NULL,
            event VARCHAR NOT NULL,
            date VARCHAR,
            venue VARCHAR,
            guest_count_estimate INTEGER,
            notes VARCHAR,
            status VARCHAR NOT NULL,
            menu_spec JSON,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS work_items (
            id UUID NOT NULL,
            job_id UUID NOT NULL,
            item_name VARCHAR NOT NULL,
            category VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            step_data JSON,
            error VARCHAR,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ,
            PRIMARY KEY (id),
            FOREIGN KEY (job_id) REFERENCES jobs (id)
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS work_items")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP INDEX IF EXISTS ix_ingredient_cache_ingredient_name")
    op.execute("DROP TABLE IF EXISTS ingredient_cache")
    op.execute("DROP INDEX IF EXISTS ix_catalog_embeddings_item_number")
    op.execute("DROP TABLE IF EXISTS catalog_embeddings")
