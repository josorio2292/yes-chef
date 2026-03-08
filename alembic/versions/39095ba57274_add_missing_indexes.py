"""add missing indexes

Revision ID: 39095ba57274
Revises: 4faa7c4d4593
Create Date: 2026-03-08 16:49:47.585199

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39095ba57274"
down_revision: str | Sequence[str] | None = "4faa7c4d4593"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_work_items_job_id ON work_items (job_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_work_items_job_id_status"
        " ON work_items (job_id, status)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_work_items_job_id_status")
    op.execute("DROP INDEX IF EXISTS ix_work_items_job_id")
    op.execute("DROP INDEX IF EXISTS ix_jobs_status")
