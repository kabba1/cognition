"""Revision ordering follows the known migration graph, never hash ordering."""

from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from cognition.db.schema import classify_schema_revision
from cognition.db.session import create_db_engine


@pytest.fixture
def scripts(tmp_path: Path) -> ScriptDirectory:
    versions = tmp_path / "versions"
    versions.mkdir()
    for revision, parent in [
        ("z_first", None),
        ("m_second", "z_first"),
        ("a_third", "m_second"),
        ("side_branch", "z_first"),
    ]:
        (versions / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = {parent!r}\n"
            "branch_labels = None\ndepends_on = None\n",
            encoding="utf-8",
        )
    return ScriptDirectory(str(tmp_path))


@pytest.mark.parametrize(
    ("observed", "expected", "status"),
    [
        (("m_second",), "m_second", "exact"),
        ((), "m_second", "behind"),
        (("z_first",), "m_second", "behind"),
        (("a_third",), "m_second", "ahead"),
        (("future_unrecognized_hash",), "m_second", "unknown"),
        (("m_sec",), "m_second", "unknown"),
        (("head",), "m_second", "unknown"),
        (("side_branch",), "m_second", "diverged"),
        (("a_third", "side_branch"), "m_second", "diverged"),
        (("m_second", "unknown"), "m_second", "unknown"),
    ],
)
def test_revision_relationship(
    scripts: ScriptDirectory, observed: tuple[str, ...], expected: str, status: str
) -> None:
    result = classify_schema_revision(observed, scripts, expected)
    assert result.status == status
    assert result.expected_revision == expected
    assert result.observed_revisions == observed
    assert result.reason


@pytest.mark.parametrize("expected", ["missing", "m_sec", "head"])
def test_supported_revision_must_be_exact_known_id(
    scripts: ScriptDirectory, expected: str
) -> None:
    with pytest.raises(ValueError, match="expected revision"):
        classify_schema_revision(("m_second",), scripts, expected)


def test_multiple_graph_heads_require_explicit_supported_revision(
    scripts: ScriptDirectory,
) -> None:
    with pytest.raises(ValueError, match="single head"):
        classify_schema_revision(("a_third",), scripts)


def test_single_graph_head_is_default(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "initial.py").write_text(
        "revision = 'initial'\ndown_revision = None\n", encoding="utf-8"
    )
    assert (
        classify_schema_revision(("initial",), ScriptDirectory(str(tmp_path))).status
        == "exact"
    )


@pytest.mark.parametrize("url", ["sqlite://", "postgresql+psycopg2://localhost/test"])
def test_engine_rejects_unsupported_database_or_driver(url: str) -> None:
    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        create_db_engine(url)


@pytest.mark.parametrize(
    "schema", ["", "public; DROP DATABASE postgres", "x,y", "A", "a" * 64]
)
def test_schema_names_reject_search_path_injection(schema: str) -> None:
    with pytest.raises(ValueError, match="schema"):
        create_db_engine("postgresql+psycopg://localhost/test", schema=schema)
