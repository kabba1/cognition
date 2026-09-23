"""Permit explicit executive configuration while preserving legacy revisions."""

import sqlalchemy as sa
from alembic import op

revision = "0008_executive_configuration"
down_revision = "0007_relationships"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_runtime_config_revisions_schema_version"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "runtime_config_revisions", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "runtime_config_revisions", "config_schema_version IN (1, 2)"
    )


def downgrade() -> None:
    # Hold the same lock ALTER TABLE needs before checking the complete history;
    # no concurrent insert can slip between the check and the restored constraint.
    op.execute("LOCK TABLE runtime_config_revisions IN ACCESS EXCLUSIVE MODE")
    incompatible = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM runtime_config_revisions "
            "WHERE config_schema_version = 2 "
            "OR sanitized_config->>'config_schema_version' = '2')"
        )
    )
    if incompatible:
        raise RuntimeError(
            "Cannot downgrade while v2 configuration history exists; "
            "no revisions were deleted or converted"
        )
    op.drop_constraint(op.f(CONSTRAINT), "runtime_config_revisions", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "runtime_config_revisions", "config_schema_version = 1"
    )
