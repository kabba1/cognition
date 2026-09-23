"""PostgreSQL-backed normalization for lexical schema-parity tests only."""

from alembic.autogenerate import compare_metadata as raw_compare_metadata
from sqlalchemy import text
from sqlalchemy.schema import CreateIndex

from cognition.protocols.common import new_id

LEXICAL_INDEXES = (
    ("beliefs", "ix_beliefs_lexical"),
    ("episodes", "ix_episodes_lexical"),
    ("event_contents", "ix_event_contents_lexical"),
)


def compare_metadata(context, metadata):
    """Keep Alembic drift except proven spelling-only lexical index differences.

    PostgreSQL deparses qualified English regconfig constants as unqualified names
    when visible, and adds casts/parentheses. Compare its own parsed forms rather
    than guessing that differently spelled SQL has the same meaning. Sibling
    indexes exist only within rolled-back savepoints in disposable test schemas.
    """
    connection = context.connection
    differences = raw_compare_metadata(context, metadata)
    equivalent = set()
    for table, name in LEXICAL_INDEXES:
        paired = [
            item
            for item in differences
            if item[0] in {"add_index", "remove_index"}
            and item[1].name == name
            and item[1].table.name == table
        ]
        if {item[0] for item in paired} != {"add_index", "remove_index"}:
            continue
        wanted = next(item[1] for item in paired if item[0] == "add_index")
        temporary_name = "lexical_compare_" + new_id().hex
        preparer = connection.dialect.identifier_preparer
        definition = str(CreateIndex(wanted).compile(dialect=connection.dialect))
        definition = definition.replace(
            preparer.quote(name), preparer.quote(temporary_name), 1
        )
        signature = text(
            "SELECT pg_get_expr(i.indexprs, i.indrelid), "
            "pg_get_expr(i.indpred, i.indrelid), a.amname, i.indisunique, "
            "i.indnullsnotdistinct, i.indclass::text, i.indoption::text, "
            "i.indcollation::text, i.indnkeyatts, i.indnatts "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_am a ON a.oid = c.relam "
            "WHERE i.indexrelid = to_regclass(:index_name)"
        )
        transaction = connection.begin_nested()
        try:
            connection.exec_driver_sql(definition)
            actual = connection.execute(signature, {"index_name": name}).one()
            expected = connection.execute(
                signature, {"index_name": temporary_name}
            ).one()
            if actual == expected:
                equivalent.add(name)
        finally:
            transaction.rollback()
    return [
        item
        for item in differences
        if not (item[0] in {"add_index", "remove_index"} and item[1].name in equivalent)
    ]
