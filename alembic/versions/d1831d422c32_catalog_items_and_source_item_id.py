"""catalog_items_and_source_item_id

Revision ID: d1831d422c32
Revises: 8388865c994d
Create Date: 2026-03-08 17:47:35.520264

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1831d422c32"
down_revision: str | Sequence[str] | None = "8388865c994d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Rename catalog_embeddings → catalog_items
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_embeddings RENAME TO catalog_items;
        EXCEPTION WHEN undefined_table THEN NULL;
        END $$
    """)

    # 2. Rename item_number → source_item_id in catalog_items
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items RENAME COLUMN item_number TO source_item_id;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$
    """)

    # 3. Add new columns to catalog_items
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items ADD COLUMN unit_of_measure VARCHAR NOT NULL DEFAULT '';
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items ADD COLUMN cost_per_case FLOAT NOT NULL DEFAULT 0;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items ADD COLUMN category VARCHAR;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items ADD COLUMN brand VARCHAR;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items ADD COLUMN source_metadata JSONB;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items ADD COLUMN ingested_at TIMESTAMPTZ NOT NULL DEFAULT now();
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$
    """)

    # 4. Drop old unique index on item_number, add composite unique on (provider, source_item_id)
    op.execute("DROP INDEX IF EXISTS ix_catalog_embeddings_item_number")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_catalog_items_provider_source_item_id
        ON catalog_items (provider, source_item_id)
    """)

    # 5. Add partial index on is_active
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_catalog_items_is_active
        ON catalog_items (is_active)
        WHERE is_active = TRUE
    """)

    # 6. Add provider index
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_catalog_items_provider
        ON catalog_items (provider)
    """)

    # 7. Rename HNSW index (cosmetic, follows new table name)
    op.execute("DROP INDEX IF EXISTS ix_catalog_embeddings_embedding_hnsw")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_catalog_items_embedding_hnsw
        ON catalog_items
        USING hnsw (embedding vector_cosine_ops)
    """)

    # 8. Rename sysco_item_number → source_item_id in ingredient_cache
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE ingredient_cache RENAME COLUMN sysco_item_number TO source_item_id;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$
    """)


def downgrade() -> None:
    # Reverse ingredient_cache rename
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE ingredient_cache RENAME COLUMN source_item_id TO sysco_item_number;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$
    """)

    # Reverse HNSW index rename
    op.execute("DROP INDEX IF EXISTS ix_catalog_items_embedding_hnsw")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_catalog_embeddings_embedding_hnsw
        ON catalog_items
        USING hnsw (embedding vector_cosine_ops)
    """)

    # Drop new indexes
    op.execute("DROP INDEX IF EXISTS ix_catalog_items_provider")
    op.execute("DROP INDEX IF EXISTS ix_catalog_items_is_active")
    op.execute("DROP INDEX IF EXISTS ix_catalog_items_provider_source_item_id")

    # Recreate old unique index
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_catalog_embeddings_item_number
        ON catalog_items (source_item_id)
    """)

    # Drop new columns
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS is_active")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS ingested_at")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS source_metadata")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS brand")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS category")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS cost_per_case")
    op.execute("ALTER TABLE catalog_items DROP COLUMN IF EXISTS unit_of_measure")

    # Rename source_item_id → item_number
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items RENAME COLUMN source_item_id TO item_number;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$
    """)

    # Rename catalog_items → catalog_embeddings
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE catalog_items RENAME TO catalog_embeddings;
        EXCEPTION WHEN undefined_table THEN NULL;
        END $$
    """)
