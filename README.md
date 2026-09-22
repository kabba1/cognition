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

## PostgreSQL development and integration tests

Phase 1 uses synchronous SQLAlchemy with the Psycopg driver. Set a local
`POSTGRES_PASSWORD` in your shell, then run `docker compose up -d postgres` to
start the optional development database. The Compose service exposes PostgreSQL
only on `127.0.0.1:55434`; `COGNITION_POSTGRES_PORT` overrides that host port.
It uses the `cognition` database and user and keeps data in a named Docker volume.
Supply passwords locally; never commit them or connection URLs containing them.
An existing PostgreSQL installation can be used instead of Docker.

Set `COGNITION_DATABASE_URL` to a `postgresql+psycopg` connection URL for the
database you explicitly intend to migrate, then run:

```text
alembic upgrade head
alembic current
```

Engine construction and schema checks never migrate automatically. Revision
`0001_foundation` creates no domain tables; Alembic maintains its own version
table. Review every later migration before applying it. `alembic upgrade head
--sql` can render migration SQL without connecting to a database.

Set `COGNITION_TEST_DATABASE_URL` separately to opt into PostgreSQL integration
tests, then run `pytest tests/integration`. The account must be allowed to create
and drop schemas in that test database. Each test creates a unique
`cognition_test_<UUID>` schema, migrates it to the current head, and gives every
connection a schema-specific search path excluding `public`. Alembic's version
table lives in the same schema. Tests may commit transactions and use multiple
independent sessions. Cleanup drops only the generated test schema. Integration
tests skip when this environment variable is absent; unit and contract tests
remain offline.

`check_schema_revision` is read-only. It reports `exact`, `behind`, `ahead`,
`unknown`, or `diverged` by consulting the known Alembic migration graph. `ahead`
requires a known descendant of the explicitly supported revision. An unfamiliar
revision is `unknown`, since its position cannot be inferred from its ID;
unsupported multiple heads or incompatible known branches are `diverged`.
