# Adaptive heartbeat engineering gate

**1,859 tests pass** in one clean full PostgreSQL 18 run on Python 3.13/Windows
(823.19 seconds). Ruff and formatting pass; strict mypy passes for 89 source files.
[Full test output](phase4c-tests.txt). The increment adds 102 cases: 19 schema,
52 pure policy, 19 scheduler integration and 12 acceptance cases.

Migration 0010 retains operational heartbeat timing separately from personality,
genesis and governance. One managed heartbeat survives process restarts. Quiet
autonomous outcomes back off within configuration limits; external/bootstrap wakes
or recorded personal activity reset the interval. Commitment deadlines can shorten
it without letting old overdue obligations cause perpetual rapid polling. Safe
calendar saturation covers extreme finite configuration values.

Terminal accounting is atomic with wake consumption and idempotent across older
outcome replay. Paused in-flight terminal responses retain operational accounting
but defer wake creation/retiming. Resume materializes from that outcome without
resampling. Missing active configuration can use a verified retained revision for
accounting while leaving materialization deferred. Invalid retained configuration,
foreign pointers and orphan managed claims fail explicitly.

Independent review led to regressions for recorded activity before later failure,
paused desired-versus-actual wake evidence, unflushed/cached scheduling inputs,
consumed pointers without deferral, retained policy integrity, and replay after a
later cycle at the same timestamp. Final independent reviews found no remaining
concrete issue. The complete suite also exercises exact-D1 interruption recovery,
fresh adapters, long silence, configuration compatibility and read-only diagnostics.

This supplies durable opportunities, not an unattended service or live model.
`run-once` still processes one bounded cycle and must be invoked again when due.
The managed heartbeat interval is not a cumulative inference-cost budget; explicit
self-scheduling retains its own minimum delay. Reflection/exploration, perception,
external execution and later roadmap systems remain further work. Deterministic
tests do not establish empirical self-directed behavior or identity quality.
