# Adaptive heartbeat

Phase 4c gives an active individual one durable managed heartbeat. After an
otherwise quiet cycle it can sleep and still have a future opportunity to consider
its situation. PostgreSQL retains the timing across process restarts; it does not
invent experience during the intervening time.

The current `run-once` command still executes one bounded cycle. A future heartbeat
is a database wake, not an operating-system timer, daemon or live provider. Another
runtime invocation is needed to claim it when due. No external action is enabled.

## Policy 1

Existing attention configuration supplies minimum seconds, maximum seconds and
backoff factor. The effective minimum is at least one second, and the maximum is
at least that minimum. Initial interval is the minimum. A no-op autonomous cycle
multiplies its interval by the factor up to the maximum. Factor 1 intentionally
disables growth. Finite large values saturate safely at the representable calendar
limit; invalid retained state fails instead of silently inventing a policy.

A cycle is autonomous when every wake is heartbeat, self-scheduled, reflection,
goal review, routine or maintenance. A bootstrap, external, action-result or recovery
wake makes that cycle non-autonomous and resets the interval to minimum. Any
applied personal operation across the cycle's turns also resets it, even if a later
turn fails. Focus-only changes and wake requests do not count. An autonomous failure
without applied personal activity backs off. This structural signal does not measure
whether a project was useful or whether an individual is motivated.

The interval controls only the managed heartbeat. Existing explicit self-scheduled
wakes still have their own minimum delay; this is not a complete cumulative
inference-budget mechanism. Reflection and bounded exploration opportunity policies
remain subsequent work.

The next default deadline is anchored at initialization or cycle completion, so
repeated polling cannot postpone it. The earliest owned active/disputed commitment
due after that anchor can shorten the deadline, subject to the effective minimum.
After a cycle has considered an overdue obligation, its old deadline cannot keep
forcing rapid heartbeats. The context's urgent-commitment policy still recalls it.
Time spent offline produces one overdue opportunity, not a backlog of invented
heartbeats for every missed interval.

## Durable state and recovery

Migration `0010_autonomy_state` adds scheduler state separately from personality,
genesis and governance. It retains interval, anchor, managed wake, last completed
cycle, policy/configuration revision and a deferred-materialization marker. The
managed wake has no coalesce key; a model cannot impersonate it through an ordinary
self-scheduled wake key. Unchanged polls write nothing. An existing active cycle
can initialize state without creating a parallel successor.

Terminal cycle accounting shares the transaction that consumes its wakes. Replaying
an already finished cycle does not multiply or reset the interval again. A committed
decision remains exact and can be applied without its original model. Frozen
requests remain byte-for-byte unchanged by newer scheduling or configuration.

A pause prevents wake creation/retiming, new claims and personal decision application.
A terminal provider response arriving after pause may still be recorded and finish
its cycle. Its operational interval/anchor accounting is retained, with wake
materialization explicitly deferred. Resume creates or retimes one wake from that
recorded outcome without resampling it. A consumed managed pointer—or an unchanged
pending wake with its previous deadline—is valid while that marker is set.

If active configuration is temporarily absent, terminal accounting can use the
verified retained revision; wake materialization waits for an active configuration.
With no prior scheduler state or active configuration, initialization waits as well.
Configuration ownership, activation, schema and canonical hash remain checked.
An inconsistent managed claim fails before generic orphan recovery can replace its
wake behind the scheduler's pointer.

Each actual scheduler transition appends `attention.heartbeat_updated` evidence
with policy, configuration identity, previous/current state, desired deadline and
references, and whether a wake was actually created or retimed. Desired timing is
distinct from the unchanged actual wake during a pause. Runtime scheduler events
do not provide independent grounding for inferred personality changes.

`cognition check` reads scheduler ownership, policy, retained configuration, cycle
anchor and managed-wake consistency without flushing or repairing caller state.
Downgrade refuses populated scheduler state; it never discards continuity just to
fit an older schema. Review the migration before applying it to an existing system.
