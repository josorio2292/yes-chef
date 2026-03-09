"""rename jobs to quotes and work_items to menu_items

Revision ID: a3f9c1e82b47
Revises: d1831d422c32
Create Date: 2026-03-09

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f9c1e82b47"
down_revision: str | Sequence[str] | None = "d1831d422c32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop indexes that reference old table/column names (must go before rename)
    op.execute("DROP INDEX IF EXISTS ix_work_items_job_id_status")
    op.execute("DROP INDEX IF EXISTS ix_work_items_job_id")
    op.execute("DROP INDEX IF EXISTS ix_jobs_status")

    # 2. Drop the FK constraint on work_items.job_id
    #    (Postgres auto-named it work_items_job_id_fkey from the initial schema)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE work_items DROP CONSTRAINT work_items_job_id_fkey;
        EXCEPTION WHEN undefined_object THEN NULL;
        END $$
    """)

    # 3. Rename tables
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE jobs RENAME TO quotes;
        EXCEPTION WHEN undefined_table THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE work_items RENAME TO menu_items;
        EXCEPTION WHEN undefined_table THEN NULL;
        END $$
    """)

    # 4. Rename column job_id → quote_id on the newly renamed menu_items table
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE menu_items RENAME COLUMN job_id TO quote_id;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$
    """)

    # 5. Recreate FK constraint with new names
    op.execute("""
        ALTER TABLE menu_items
        ADD CONSTRAINT menu_items_quote_id_fkey
        FOREIGN KEY (quote_id) REFERENCES quotes (id)
    """)

    # 6. Recreate indexes with new names
    op.execute("CREATE INDEX IF NOT EXISTS ix_quotes_status ON quotes (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_menu_items_quote_id ON menu_items (quote_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_menu_items_quote_id_status"
        " ON menu_items (quote_id, status)"
    )


def downgrade() -> None:
    # 1. Drop new indexes
    op.execute("DROP INDEX IF EXISTS ix_menu_items_quote_id_status")
    op.execute("DROP INDEX IF EXISTS ix_menu_items_quote_id")
    op.execute("DROP INDEX IF EXISTS ix_quotes_status")

    # 2. Drop new FK constraint
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE menu_items DROP CONSTRAINT menu_items_quote_id_fkey;
        EXCEPTION WHEN undefined_object THEN NULL;
        END $$
    """)

    # 3. Rename column quote_id → job_id
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE menu_items RENAME COLUMN quote_id TO job_id;
        EXCEPTION WHEN undefined_column THEN NULL;
        END $$
    """)

    # 4. Rename tables back
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE menu_items RENAME TO work_items;
        EXCEPTION WHEN undefined_table THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE quotes RENAME TO jobs;
        EXCEPTION WHEN undefined_table THEN NULL;
        END $$
    """)

    # 5. Recreate original FK constraint
    op.execute("""
        ALTER TABLE work_items
        ADD CONSTRAINT work_items_job_id_fkey
        FOREIGN KEY (job_id) REFERENCES jobs (id)
    """)

    # 6. Recreate original indexes
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_work_items_job_id ON work_items (job_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_work_items_job_id_status"
        " ON work_items (job_id, status)"
    )
