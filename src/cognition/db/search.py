"""Static immutable search projections shared by index metadata and queries.

These are trusted SQL expressions, never query-source interpolation. Prefixes are
characters, not bytes; canonical fields remain complete and independently mutable.
Migration 0009 freezes its own literals so later policy changes cannot rewrite it.
"""

BELIEF_VECTOR_SQL = (
    "to_tsvector('pg_catalog.english'::regconfig, "
    "left(coalesce(proposition, ''), 8192) || ' ' || "
    "left(coalesce(topic, ''), 1024))"
)
EPISODE_VECTOR_SQL = (
    "to_tsvector('pg_catalog.english'::regconfig, left(coalesce(summary, ''), 8192))"
)
EVENT_VECTOR_SQL = (
    "to_tsvector('pg_catalog.english'::regconfig, left(coalesce(text, ''), 8192))"
)
