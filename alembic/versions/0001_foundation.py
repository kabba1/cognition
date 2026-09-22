"""Establish migration identity without creating domain tables."""

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Alembic records this foundation revision in its own version table."""


def downgrade() -> None:
    """No domain objects exist at this revision to remove."""
