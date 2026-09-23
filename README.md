# Cognition

Cognition is one Python application for persistent AI individuals. Phase 0 and
Phase 1 provide typed contracts, durable identity and evidence, atomic birth,
configuration history, singleton runtime ownership, local administration, and
integrity diagnostics. Phase 2 adds bounded cognition cycles, frozen context,
invocation history, exact decision recovery, and atomic focus/wake application.
The local fixture runner exercises this path without a live model or external effects.
Phase 3 adds evidence-linked personal state and staged development through reflection; see
[personal state](docs/personal-state.md) for supported operations and remaining work.
[Executive protocols](docs/executive-protocols.md) explains explicit v2 selection,
model-authored entities/projects/relationships, and recovery across version changes.
[Attention](docs/attention.md) describes bounded reference and lexical recall, urgent
commitments, and inspectable context selection.

**Architect review starts here:** [current implementation status](docs/current-status.md).
The [Phase 0–1 handoff](docs/architect-handoff.md) preserves the original baseline.
See [Phase 2 operations](docs/phase2-operations.md) for current execution behavior.
Implementation and test completion do not imply architectural acceptance.

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
installing the project is required before running them. Unit and contract tests
run offline. Integration and acceptance tests require the explicit PostgreSQL
test URL described below and otherwise skip. Provider credentials are never required.

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

Interfaces are synchronous. Pydantic v2 defines versioned contracts; SQLAlchemy 2,
Psycopg 3, and Alembic provide explicit PostgreSQL persistence and migrations.
The application has no web framework or live provider SDK.

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
and acceptance tests, then run `pytest`. Use a dedicated disposable test database.
The account must be allowed to create and drop schemas. Corruption-fixture tests
also require permission to set `session_replication_role`; the default PostgreSQL
development superuser supports this. Each test creates a unique
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

## Local operations

Use `cognition check` for structured, read-only integrity findings. Exit status is
0 for healthy, 1 for findings, or 2 when diagnostics cannot run. Checks include
claimed-wake ownership, cycles, turns, snapshots, invocation results, and operations.

Use `cognition admin --help` to see lifecycle operations. For example:

```text
cognition admin pause --individual-id YOUR-UUID --reason "Operator maintenance"
cognition admin emergency_block --individual-id YOUR-UUID --reason "Contain effects"
```

Commands read `COGNITION_DATABASE_URL`, require the exact supported schema, and
never migrate implicitly. Administrative identity comes from the current OS token
(Windows SID or POSIX UID), matched to an active administrator created at birth.
The CLI accepts no principal override. Administrative changes, audit rows, and
evidence commit in one transaction.

See [Phase 1 operations](docs/phase1-operations.md) for the Python birth and runtime
ownership APIs, and [implementation decisions](docs/implementation-decisions.md)
for the explicit lifecycle transition policy and architectural choices.
