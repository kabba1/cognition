# Managed reflection engineering gate

**1,994 tests pass** in one clean full PostgreSQL 18 run on Python 3.13/Windows
(954.16 seconds). Ruff and formatting pass; strict mypy passes for 94 source files.
[Full test output](phase4d-tests.txt). This increment adds 135 cases: 27 schema,
18 selection, 35 scope, 21 scheduler, 25 diagnostics and nine acceptance cases.

Migration 0011 retains operational reflection state and immutable managed-batch
metadata. Owned candidate interests, tentative preferences and pending inferred
self-state receive at most eight review targets per batch, with bounded family
rotation and at least 24 hours between completed managed opportunities. The policy
creates no personal conclusions or grounding evidence. Empty birth has no reflection
state; sleep/no-op remains valid.

Exact target scope survives pointer advancement and creation-content redaction.
Retained creation envelopes prevent missing historical metadata from becoming broad
generic reflection permission. Ordinary reflection and exact-target self-scheduling
keep their prior semantics. Grounded establishment succeeds; ungrounded and excluded
target proposals reject. Current target revisions/eligibility remain authoritative
for semantic validation; selected metadata is an immutable audit observation.

Terminal accounting, cursor advancement, next-review time and wake consumption are
atomic. Pause or missing active configuration can defer materialization. Older
outcome replay does not count again, and committed decisions recover without a
model or resampling. Diagnostics read current and historical scope, ownership,
live cardinality and cycle/outcome links without flushing or refreshing caller state.

Independent review led to regressions for mixed-wake ownership filtering, mutable
kind classification, hidden live batches and deadline tampering. An initial full
run was interrupted to fix the timing finding. The final clean run includes the
fix: immutable batch selection/eligibility floors constrain every managed deadline,
and the current pending/claimed batch also respects the retained cadence. Consumed
history is not compared with the successor's next-review floor.

An older external-only rejection test now preclaims its cycle, preserving its
original absence-of-reflection premise: a fresh runtime pass legitimately supplies
a mature managed review opportunity before claiming. The original grounding guard
remains tested. Public v1/v2 golden contracts remain unchanged.

This gate demonstrates deterministic scheduling and recovery, not empirical
personality quality. Fixed limits are experimental and bound returned candidates
and managed opportunities, not all database work or cumulative inference spending.
The command still processes one cycle per invocation. Explicit exploration,
perception, external execution, a live provider and an unattended service remain
further work.
