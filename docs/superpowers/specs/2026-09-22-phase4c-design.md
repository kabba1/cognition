# Phase 4c: durable adaptive heartbeat

The academic specification requires liveness without synthetic inner activity:
sleep is valid, repeated no-op autonomous wakes back off, and meaningful activity
or approaching commitments can shorten the next opportunity. Implement that
mechanism after the lexical gate, before reflection/exploration scheduling. This
increment does not start a daemon or call a live provider. `run-once` remains one
bounded cycle; a future service can invoke it when durable wakes become due.

## Scope and policy

Add a separate operational `AutonomyState` row per individual in migration 0010.
This is scheduler state, not personality, motivation, value strength or governance.
Retain policy version 1, interval seconds, anchor time, managed heartbeat wake ID,
last completed cycle ID, scheduling configuration revision ID, a materialization-
pending flag and positive revision. The managed wake ID is nullable while state-only
initialization or deferred materialization is pending.
All references remain inspectable. Matching constraints and read-only diagnostics
check finite positive intervals, ownership, configuration linkage, cycle status,
and heartbeat kind. Downgrade refuses populated scheduler state rather than silently
discarding continuity; an empty table can be removed.

Use existing attention configuration min/max/backoff fields. The effective minimum
is at least one second, matching the existing baseline wake floor. Effective maximum
is at least that minimum. No new config/public executive version is required.
Clamp retained intervals into the current effective range. Multiplication saturates
at the configured maximum without producing infinity. Calendar addition saturates
at the greatest representable UTC timestamp, avoiding overflow for valid but huge
configured finite seconds. Record the effective policy in scheduler evidence.

Initialize lazily under active runtime ownership with interval=min and anchor=now.
Birth continues to create its existing bootstrap wake atomically; no invented
experience or personal state is added. Existing individuals acquire this operational
row on their first eligible runtime pass. If an existing active cycle predates the
migration, initialize scheduler state only and defer its first managed wake until
that cycle finishes. Initialization does not replay historical
cycles or infer unrecorded activity.

On terminal cycle completion, advance scheduler state in the same transaction as
cycle completion and wake consumption. A repeated finish cannot update it twice.
Across all applied turns in that cycle, any applied personal operation counts as
recorded activity; focus-only changes and wake requests do not. This is a structural
engineering signal, not a judgment that an activity was worthwhile. Applied goals,
commitments, beliefs, episodes and all other supported personal families qualify.

A cycle is autonomous only when its nonempty set of claimed wake kinds all belong to
heartbeat,
self_scheduled, reflection, goal_review, routine or maintenance. A no-op autonomous
cycle multiplies the interval by the backoff factor, capped at max. A failed or
rejected autonomous cycle with no applied personal activity also backs off; provider
failures must not create a tight retry storm. Any recorded personal activity takes
precedence over a later failed turn and resets the interval to min; so does a
non-autonomous cycle. Sleep alone is not failure. The new anchor is cycle completion.
Backoff controls only the managed heartbeat. Existing explicit self-scheduled wakes
can still request earlier opportunities under their current one-second floor;
this increment is not a complete cumulative inference-budget mechanism.

## One managed wake, deadlines and restart behavior

The scheduler owns one heartbeat wake by its durable ID, not a model-supplied
coalesce key. Use null coalesce key and a fixed purpose that explicitly permits
sleep/no action. Ordinary self-scheduled wakes cannot impersonate the managed wake
through their key. Do not merge arbitrary wake purposes or accumulate references.

Default due time is anchor + interval. Look up at most one earliest owned active/
disputed commitment with due_at strictly after the anchor, ordered by due time/ID.
Its due time may shorten the heartbeat, bounded below by anchor + effective_min.
Retain that commitment as one context ref if it determines the earlier deadline.
Passing time alone must not move a pending due date forward. Once a cycle has
considered an overdue obligation, the new anchor excludes that old deadline from
repeated acceleration; otherwise an unresolved obligation could defeat backoff
forever. The ordinary urgent-context policy still recalls overdue obligations.

Before claiming a new cycle, ensure the managed wake exists and reconcile changed
configuration/commitment timing under the individual lock. If the managed wake is
already claimed by an active cycle, preserve it and resume the cycle. Validate this
link before generic orphan recovery; a managed claimed wake without an owned active
cycle is an integrity error, not permission to replace it behind the managed pointer.
Never create a parallel successor while a cycle is active. Terminal-cycle handling reuses an
existing pending managed heartbeat or creates its successor after consumption.
Cancelled/superseded managed wakes can be replaced on an eligible runtime pass,
with explicit evidence. Foreign, wrong-kind or inconsistent references are errors,
not permission to repair another individual's state.

Repeated polling with unchanged inputs writes nothing. A restarted runtime sees
the same interval, anchor and managed wake. A missed deadline yields one overdue
heartbeat opportunity, not one wake per elapsed interval. No activity is invented
for the interval spent asleep. New configuration may clamp/recalculate a pending
wake, with its revision identity retained; it never rewrites a frozen model request.

## Transactions, authority and evidence

Keep store code independent of runtime imports. Scheduler writes join the caller
transaction and take the existing individual-before-governance lock order. It runs
only for active individuals with inference allowed when creating or retiming wakes.
A pause leaves pending state durable and prevents scheduling/claim/personal-state
application until resumed. Terminal result bookkeeping remains allowed: a refusal,
nonretryable error or duplicate decision can finish an in-flight cycle after pause.
Its scheduler interval, anchor and last-cycle marker advance atomically with that
terminal result, but wake creation/retiming is deferred with materialization pending.
The managed pointer may then name its consumed wake or an unchanged pending wake
with its previous due time; both are valid while materialization is pending.
Resume materializes exactly one successor using the retained outcome without
resampling or replaying the cycle. Diagnostics allow this explicit deferred state
and do not mistake a consumed pointer for a live wake. Losing the runtime ownership
connection remains fatal to execution. Model availability is not required
to persist a future opportunity, but no inference begins without a compatible model.

Append one runtime evidence event for each scheduler transition: initialization,
cycle outcome, changed scheduling input, or replacement. Include prior/new interval,
anchor/due, managed wake, triggering cycle when present, config revision, policy and
reason. Distinguish the computed desired schedule from actual wake creation or
retiming, especially while paused. Resume must not apply interval backoff/reset a
second time. These events are operational evidence; they are not qualifying independent
anchors for inferred personality. Pure unchanged polls produce no event. Update the
wake and scheduler state atomically with their event; no separate commit is allowed.

If active configuration is temporarily absent, do not prevent the existing
`missing_configuration` terminal failure from committing. Existing scheduler state
uses its retained configuration revision for terminal accounting and defers wake
materialization until active configuration returns. Without any previous scheduler
state or active configuration, skip initialization entirely; later initialization
starts at min and does not manufacture historical outcomes. Invalid retained
configuration or foreign linkage is corruption and must not silently choose policy.
Validate retained ownership, schema and canonical configuration hash, and record the
exact revision used by accounting even when materialization is deferred.

Call the scheduler from the runtime before a new claim and from terminal-cycle
completion. Existing committed decisions still apply exactly once before a model
is required. Already frozen context bytes remain unchanged. Do not add scheduler
numbers to the personality projection or promote ordinary wake text to control.

## Verification

Pure tests exercise deterministic doubling/capping/reset, factor 1, fractional and
huge finite inputs, one-second floor, and safe UTC calendar saturation. PostgreSQL
tests cover schema parity/downgrade guards; owned state/wake/config/cycle references;
unchanged polls; idempotent finish; rollback; paused/inference-blocked behavior;
pending/claimed/consumed/replaced wakes; config changes; deadline shortening without
poll drift or overdue busy loops; and all applied turns contributing activity.

Acceptance uses FakeClock and fresh scripted models across restarts to show newborn
sleep followed by one durable future wake, repeated no-op backoff, recorded activity
reset, long silence coalesced into one opportunity, pause/resume, failure backoff,
and recovery of exact D1 with scheduler state committed once. Include pause during
a terminal refusal/nonretryable provider result, resume materialization without
resampling, absent active configuration, an active cycle predating scheduler state,
and rejected managed orphan claims. No wall-clock sleeps,
live credentials, external actions or claimed empirical autonomy quality.

Reflection and exploration opportunity policy, an unattended service, live-provider
adapters and empirical autonomy evaluation remain subsequent explicit increments.
