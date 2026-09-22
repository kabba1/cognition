# Phase 2 engineering gate

Passed 898 tests on real PostgreSQL 18 and Python 3.13 on Windows; no tests skipped.
Ruff check and format check pass; strict mypy passes for 70 source files.
Full test output: [phase2-tests.txt](phase2-tests.txt).

Twenty-four Phase 2 acceptance cases exercise claim rollback/commit, frozen context,
interrupted invocation, owner connection loss, result commit, application rollback,
terminal commit, pause and inference block, bounded retries/turns/deadline, refusal,
wrong IDs, unsupported operations, identity collision and concurrent wake arrival.
Each recovery checks the durable state and integrity diagnostics.

Independent review found three context/result defects, all regression tested:
governance references exposed in context were rejected by semantic validation;
older explicitly referenced evidence was absent from context candidates; NUL and
surrogate strings passed protocol validation but failed PostgreSQL persistence.
The acceptance work separately caught a wake-ID collision that caused repeated
application failure. These now reject or recover through explicit durable paths.

This gate demonstrates deterministic execution mechanics with fixture adapters.
It does not demonstrate live model intelligence, long-term autonomy, personal-state
continuity, or consequential external action safety. Personal state is the next gate.
