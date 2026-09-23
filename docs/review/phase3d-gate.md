# Explicit executive v2 engineering gate

**1,665 tests pass** in one clean full PostgreSQL 18 run on Python 3.13/Windows
(657.68 seconds), including all previous phase tests. Ruff check and format check
pass for 176 Python files; strict mypy passes for 82 source files.
[Full test output](phase3d-tests.txt).

This increment adds 293 cases beyond Phase 3c. It introduces explicit configuration
schema 2, cognition decision/request/result v2, and entity/project/relationship/
relationship-thread proposals. All eleven personal families validate together and
apply through one exact-retained-decision transaction. V1 schema-bearing models
and their golden schemas remain unchanged. External actions remain blocked.

Configuration freezes at context-snapshot commit. Runtime loading and read-only
diagnostics check version tuples, output schema, historical behavior hash, embedded
configuration revision/hash, ownership, and adapter/model linkage. Active config
changes cannot reinterpret a frozen request or committed decision. Migration 0008
supports both configuration versions and refuses downgrade while v2 history exists.

The runtime and authenticated local CLI recover committed decisions before needing
a model or script. Continued inference blocks when an adapter is unavailable.
Fixture preflight rejects incompatible next templates and detects exhaustion before
invocation-start, preserving pending turns and attempts. No live provider or
background service is introduced.

Independent review findings were fixed and regression-tested: malformed retained
protocol tuples now block safely; snapshots cannot be relinked to another valid
behavior revision; JSON integrity comparison distinguishes booleans and numbers;
script protocol mismatch/exhaustion no longer manufacture provider attempts.
Final independent review found no remaining concrete issue in those paths.

Acceptance coverage includes v2 entity/project creation followed by relationship
and thread creation across turns, mixed-family atomic rejection, retained v1/v2
recovery after opposite-version configuration changes, configuration changes on
either side of snapshot freeze, and recovery with no adapter or script. Corrupted
frozen configuration blocks without applying or resampling committed decisions.

Entity identifier binding and goal dependencies remain explicit unfinished
semantics. Phase 4 begins with reference-driven attention and urgent commitment
recall; lexical search, adaptive heartbeat/reflection/exploration, perception,
action execution, portability, workspace, UI and live evaluation remain later work.
