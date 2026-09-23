"""Bounded immutable English search projections over canonical memory text."""

import sqlalchemy as sa
from alembic import op

revision = "0009_lexical_retrieval"
down_revision = "0008_executive_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_beliefs_lexical",
        "beliefs",
        [
            sa.text(
                "to_tsvector('pg_catalog.english'::regconfig, "
                "left(coalesce(proposition, ''), 8192) || ' ' || "
                "left(coalesce(topic, ''), 1024))"
            )
        ],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_episodes_lexical",
        "episodes",
        [
            sa.text(
                "to_tsvector('pg_catalog.english'::regconfig, "
                "left(coalesce(summary, ''), 8192))"
            )
        ],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_event_contents_lexical",
        "event_contents",
        [
            sa.text(
                "to_tsvector('pg_catalog.english'::regconfig, "
                "left(coalesce(text, ''), 8192))"
            )
        ],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_event_contents_lexical", table_name="event_contents")
    op.drop_index("ix_episodes_lexical", table_name="episodes")
    op.drop_index("ix_beliefs_lexical", table_name="beliefs")
