"""Read-only migration compatibility checks based on the known Alembic graph."""

from dataclasses import dataclass
from enum import StrEnum

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection


class SchemaRevisionStatus(StrEnum):
    EXACT = "exact"
    BEHIND = "behind"
    AHEAD = "ahead"
    UNKNOWN = "unknown"
    DIVERGED = "diverged"


@dataclass(frozen=True)
class SchemaRevisionCheck:
    status: SchemaRevisionStatus
    observed_revisions: tuple[str, ...]
    expected_revision: str
    reason: str


def classify_schema_revision(
    observed_revisions: tuple[str, ...],
    scripts: ScriptDirectory,
    expected_revision: str | None = None,
) -> SchemaRevisionCheck:
    """Compare exact revision IDs through explicit migration ancestry.

    Unknown hashes cannot be ordered. In particular, a revision from a future
    release is UNKNOWN unless its script is in this graph. AHEAD is provable only
    for a known descendant of an explicitly supported earlier revision. Multiple
    database heads are conservatively DIVERGED; no automatic migration is made.
    """
    known = {revision.revision for revision in scripts.walk_revisions()}
    if expected_revision is None:
        heads = scripts.get_heads()
        if len(heads) != 1:
            raise ValueError("a single head or explicit expected revision is required")
        expected_revision = heads[0]
    if expected_revision not in known:
        raise ValueError("expected revision must be an exact ID in the migration graph")

    def result(status: SchemaRevisionStatus, reason: str) -> SchemaRevisionCheck:
        return SchemaRevisionCheck(
            status, observed_revisions, expected_revision, reason
        )

    if any(revision not in known for revision in observed_revisions):
        return result(
            SchemaRevisionStatus.UNKNOWN,
            "Database contains an unrecognized revision; ordering cannot be inferred.",
        )
    if not observed_revisions:
        return result(SchemaRevisionStatus.BEHIND, "Database has no applied revision.")
    if len(observed_revisions) != 1:
        return result(
            SchemaRevisionStatus.DIVERGED,
            "Database has multiple revision heads; one supported lineage is required.",
        )
    observed = observed_revisions[0]
    if observed == expected_revision:
        return result(
            SchemaRevisionStatus.EXACT, "Database matches the supported revision."
        )
    expected_ancestors = {
        revision.revision
        for revision in scripts.iterate_revisions(expected_revision, "base")
    }
    if observed in expected_ancestors:
        return result(
            SchemaRevisionStatus.BEHIND,
            "Database revision is a known ancestor of the supported revision.",
        )
    observed_ancestors = {
        revision.revision for revision in scripts.iterate_revisions(observed, "base")
    }
    if expected_revision in observed_ancestors:
        return result(
            SchemaRevisionStatus.AHEAD,
            "Database revision is a known descendant of the supported revision.",
        )
    return result(
        SchemaRevisionStatus.DIVERGED,
        "Database revision is on a different known migration branch.",
    )


def check_schema_revision(
    connection: Connection,
    scripts: ScriptDirectory,
    expected_revision: str | None = None,
    *,
    version_table_schema: str | None = None,
) -> SchemaRevisionCheck:
    """Read the schema-local Alembic version table without creating or changing it."""
    context = MigrationContext.configure(
        connection, opts={"version_table_schema": version_table_schema}
    )
    return classify_schema_revision(
        context.get_current_heads(), scripts, expected_revision
    )
