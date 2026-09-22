"""Small AST checks preserve Cognition's persistence/provider boundaries."""

import ast
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "src"
DATABASE = ("sqlalchemy", "psycopg", "psycopg2", "alembic", "cognition.db")
PROVIDERS = ("openai", "anthropic", "google.genai", "boto3", "litellm")


def imported_modules(text: str, package: str) -> set[str]:
    result = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = importlib.util.resolve_name("." * node.level + module, package)
            result.add(module)
            result.update(f"{module}.{alias.name}" for alias in node.names)
    return result


@pytest.mark.parametrize(
    "folder,forbidden",
    [
        ("protocols", DATABASE + PROVIDERS + ("cognition.runtime", "cognition.stores")),
        ("domain", DATABASE),
        ("models", PROVIDERS),
    ],
)
def test_import_boundaries(folder: str, forbidden: tuple[str, ...]) -> None:
    for file in (SOURCE / "cognition" / folder).rglob("*.py"):
        package = ".".join(file.parent.relative_to(SOURCE).parts)
        for module in imported_modules(file.read_text(encoding="utf-8"), package):
            assert not any(
                module == bad or module.startswith(bad + ".") for bad in forbidden
            ), (file, module)


def test_import_inspection_handles_relative_and_nested_imports() -> None:
    text = (
        "from .. import db\nfrom ..runtime import birth\n"
        "if True:\n import sqlalchemy.orm\n"
    )
    assert {
        "cognition.db",
        "cognition.runtime.birth",
        "sqlalchemy.orm",
    } <= imported_modules(text, "cognition.protocols")


def test_declared_runtime_dependencies_are_only_approved_foundation() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {
        re.split(r"[\[<>=!~; ]", dependency, maxsplit=1)[0].lower()
        for dependency in config["project"]["dependencies"]
    }
    assert names == {"pydantic", "sqlalchemy", "psycopg", "alembic"}
