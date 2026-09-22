# Grounded personal-state engineering gate

**1,146 tests pass** in the final full suite on real PostgreSQL 18, Python 3.13,
Windows. Ruff check and format check pass; strict mypy passes for 73 source files.
[Full test output](phase3a-tests.txt).

The increment includes 65 schema cases, 147 personal-store transition cases,
14 personal recovery cases, and 14 personal integrity cases. Existing Phase 1–2
tests also pass. Birth leaves personal and execution projections empty.

Review findings fixed and regression tested:

- Personal application must match exact persisted decision JSON/identity/digest
  on a decided turn in an active owned cycle.
- Context packs objects individually so one oversized goal does not suppress
  smaller goals; active obligations rank before newer paused/proposed records.
- Standalone diagnostics suppress autoflush and normalize timestamps to UTC,
  preserving caller state and avoiding false mismatches in non-UTC DB sessions.

Crash acceptance interrupts real PostgreSQL writes, reacquires runtime ownership,
and verifies exact D1 reuse, no partial projections/history, no duplicate effects,
and restart recall without relying on a model transcript. Unsupported operation
families reject the entire decision, and genesis/governance remain separate.

This is the first Phase 3 increment. Interest/preference/self-model inertia,
relationships and model-authored project/entity operations remain subsequent work.
The gate demonstrates deterministic state semantics, not empirical validation of
long-term identity or autonomy.
