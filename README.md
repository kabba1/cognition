# Cognition

Cognition is one Python application for persistent AI individuals. COG-0001
establishes its package boundaries and development tooling. The CLI currently
provides help only.

## Development setup

Python 3.12 or newer is required. Create a virtual environment and install the
project in editable mode with its development tools:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cognition --help
```

On macOS or Linux, activate with `source .venv/bin/activate` instead.
The help command is also available as `python -m cognition.cli.main --help`.

## Checks

Run from the repository root in the activated environment:

```text
pytest
ruff check .
mypy src/cognition
```

The smoke tests exercise the installed package from a temporary directory;
installing the project is required before running them. Tests do not need a
database, provider credentials, or network access.

## Package layout

All modules belong to the single `src/cognition` application package:

```text
src/cognition/
  cli/
  config/
  protocols/
  domain/
  db/
  stores/
  runtime/
  policy/
  models/
  connectors/
  capabilities/
  artifacts/
  continuity/
  observability/
  testing/
tests/
  unit/
  contracts/
  integration/
  acceptance/
```

Interfaces will be synchronous initially. Pydantic v2, SQLAlchemy 2, Psycopg 3,
and Alembic are declared dependencies for subsequent work. PostgreSQL and
Alembic are not configured. The package contains no domain implementation,
protocols, provider adapters, runtime loop, or web/agent framework.

COG-0001 must be reviewed before work begins on later tickets.
