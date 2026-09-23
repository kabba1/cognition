# Managed staged-state reflection

Phase 4d supplies bounded opportunities to reconsider pending personal
interpretations. It does not establish them, supply grounding evidence or require
the executive to change anything. A no-op or sleep decision remains valid.

## Experimental policy 1

One managed reflection batch can be pending or claimed per individual. A batch
contains at most eight owned references, rotating among candidate interests,
tentative preferences and pending self-belief/current-value proposals. Presentation,
narrative, established interests and retirement are not automatically scheduled.

Future eligibility produces a real wake timer. Its due time is the latest of now,
the earliest candidate eligibility and the retained next-review time. Each family
has a stable UUID cursor; bounded queries return up to eight rows after the cursor
and eight wrapping rows. Family round-robin selects eight from at most 48 returned
rows. These limits bound returned candidates, not all database work. UUID order is
an inspectable rotation policy, not a claim of personal significance.

Cursors advance only after that managed batch's cycle finishes. The next review
is at least 24 hours after actual completion, including terminal failures. Long
silence yields one opportunity rather than a backlog of missed days. Calendar
addition saturates safely. These fixed experimental limits are not a cumulative
inference-cost budget; explicitly requested self-scheduling keeps its existing rules.

Unchanged polling preserves the batch identity, targets and deadline. If all targets
have ceased to be pending, an unclaimed batch can be cancelled and replaced, with
evidence retained. Cancellation does not advance cursors or cadence. A claimed batch
is never retargeted, and frozen contexts are never recompiled.

## Scope and evidence

Migration `0011_reflection_state` adds operational `reflection_state` and retained
`managed_reflection_batches`. Each batch binds its ordered target metadata, policy,
owner, wake and selection time to a canonical hash. Recorded revisions and
eligibility times are audit observations; current maturity and grounding rules
remain authoritative. Hashes diagnose inconsistent stored data and are not signatures
against arbitrary database operators.

A managed reflection wake permits deliberate reconsideration only for its immutable
target references. An ordinary reflection wake keeps its prior broad deliberation
semantics, and a self-scheduled wake still names its exact target. Executive guidance
states this distinction. Every claimed wake is checked for retained managed identity
before any grant, including a mixed cycle with generic reflection.

Mutable wake deadlines cannot precede the batch's selection time or retained target
eligibility times. A current pending/claimed successor also cannot precede the
retained next-review time. Historical consumed batches are checked against their own
timing, not the next batch's cadence. Current target changes do not rewrite those
historical observations.

The creation event's retained envelope identifies the managed wake independently of
redactable event content and the scheduler's current pointer. A missing historical
batch cannot silently turn into broad generic reflection. Foreign targets, malformed
metadata, changed scope and hidden extra live batches fail explicitly. Reviewing or
retrieving a claim never adds an independent grounding anchor. Existing staging,
distinct evidence and claim-specific support requirements still apply.

## Transactions and recovery

Before a new claim, the runtime reconciles heartbeat and reflection under the
individual/governance lock order. Existing active cycles retain their original wakes.
Initialization waits for candidates, an active configuration and permission for
inference. Birth creates no reflection state or imagined experiences.

Managed batch completion, cursor advancement and next-review time share the wake
consumption transaction. Retained completion-envelope identity prevents replaying
an older outcome after later cycles or content redaction. Exact persisted decisions
can still recover without a model; scheduling never resamples them.

A terminal provider result received during pause can finish its operational
accounting while deferring new wakes. Resume materializes from current pending
records. Absent active configuration similarly defers materialization without
preventing terminal accounting. Unrelated cycle completion does not advance this
reflection policy. Dirty scheduling inputs are rejected before implicit flush.

`cognition check` observes current and historical batches, retained creation and
completion markers, ownership, scope hashes, live cardinality and claimed/consumed
cycle links without refreshing or flushing caller objects. Downgrade refuses if
either scheduling state or historical batch metadata remains.

The local command still runs one bounded cycle. No daemon, live provider, external
action or empirical claim about personality quality is supplied by this increment.
