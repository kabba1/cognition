# Phase 4d: bounded staged-state reflection opportunities

Heartbeat supplies liveness, but pending personal interpretations currently depend
on the executive explicitly remembering to request their later review. Add one
managed reflection opportunity that can surface mature staged state without
establishing it automatically. This increment covers reflection scheduling; explicit
internal exploration allowances and a supervised service remain the following work.

## Policy 1 and candidates

Use a fixed experimental minimum of 24 hours between completed managed reflection
batches, at most one pending/claimed managed reflection wake per individual, and at
most eight target refs per batch. These are opportunity budgets, not claims of
psychological significance or a global inference-cost limit. Existing explicit
self-scheduling remains possible. No new configuration or executive schema is
needed; persist the policy version, cadence and selected target metadata in evidence.

Candidates are owned candidate interests at `promotion_not_before`, tentative
preferences at `promotion_not_before`, and pending self-belief/current-value
replacements at `pending_not_before`. Established/dormant/retired interests,
established/retired preferences, presentation and narrative are not automatically
treated as pending promotion or retirement requests. Eligibility is an opportunity
to consider a claim, not proof of grounding. Existing time, distinct evidence,
claim-specific support and new-anchor rules remain unchanged.

Find the earliest candidate eligibility with three bounded aggregate queries. The
next batch due time is the latest of now, that earliest eligibility, and the stored
next-review time. Select candidates eligible by that due time. Future candidates
can therefore receive a real timer before they mature, and a mature candidate can
join a currently due cycle without an unnecessary polling delay.

Each of the three families has an independent stable UUID cursor. Query at most
eight eligible rows after that cursor and, only if needed, up to eight wrapping
rows. Across three families the materialized candidate pool is at most 48 rows;
this bounds returned rows, not all database work. Allocate the final eight slots
round-robin across families. Advance a family's cursor only when a cycle consumes
that managed reflection batch, to its last selected target in batch order. Cursor
IDs are positions, not assertions that the target still exists. This allows a large
unchanged pending backlog to receive turns instead of repeatedly choosing the same
oldest eight objects or letting one family crowd out the others.

## Durable state and wake semantics

Migration 0011 adds `ReflectionState` separately from autonomy/personality state:
individual primary key, policy version, next-review timestamp, nullable managed wake
ID, nullable last completed cycle ID, three nullable UUID cursors, a materialization-
pending flag and positive revision. A managed pointer may be absent when no candidate
needs review. Guard downgrade if either state or historical batches exist instead
of erasing scheduling continuity or scope needed by frozen decisions.

Initialize only when candidates exist and no cycle is active. No birth memory or
routine empty reflection is fabricated. Use a fixed purpose explaining that no
change is required, references are review targets, and evidence gates still apply.
The managed wake has kind `reflection` and null coalesce key, so model keys cannot
impersonate it. Its selected refs are retained until that batch is consumed. An
unchanged poll neither rotates targets nor changes the due time. State changes
after creation are rendered honestly by the ordinary context compiler; a target
that has since been established is not promoted again merely because it was named.

A pending batch can be cancelled if none of its targets remains pending; record
the cancellation before selecting a replacement, and never delete its wake/evidence.
Do not cancel a claimed batch or retarget a frozen context. Keep at most one live
managed pointer. Validate cardinality across all historical batch rows joined to
pending/claimed wakes: any second live batch, or a live batch omitted by the state
pointer, fails closed. The pointer alone cannot establish this invariant.
If a managed claim lacks an owned active cycle, fail before generic
orphan recovery rather than losing ownership of its replacement wake.

After the batch's cycle finishes, update cursors, the last-cycle marker and next
review time in the same transaction as wake consumption. The next review is at least
24 hours after actual completion, including terminal failures; a long pause cannot
produce a stack of immediately due replacement batches. Use the retained outcome
event to prevent recounting an older batch after a later same-instant outcome.
Its dedicated event type, wake subject and cycle correlation live in the retained
event envelope, independent of redactable content. Unrelated cycles and batch
cancellation do not advance cursors or cadence. Calendar addition saturates safely.
There is no catch-up reflection for every day spent offline.

Terminal outcome bookkeeping is allowed during an in-flight pause, as for heartbeat.
Creating/cancelling/retiming a wake requires active lifecycle, inference allowed,
valid active configuration and no active cycle. Pause or absent active configuration
leaves materialization pending. Resume materializes from current eligible records
without replaying the completed batch or resampling a committed decision. Missing
configuration must not make the existing terminal-failure path recurse or roll back.

## Deliberation scope and unchanged grounding

The existing development validator treats any ordinary claimed reflection wake as
deliberate reflection, and targeted self-scheduled wakes as deliberate for that
target. Preserve those existing semantics. For a newly managed reflection wake,
require that the proposed inferred target is one of that batch's retained refs;
it is not blanket permission to establish unrelated personal interpretations.
The managed identity must be recognized durably even after its pointer advances,
using a small append-only managed-reflection batch record keyed by wake ID, with
individual, policy version and immutable target refs/revision/eligibility metadata
plus a canonical content hash. Revisions and eligibility are audit observations,
not an additional revision-equality gate: current maturity/grounding rules decide.
The creation event's retained envelope identifies the managed wake by owner and
wake subject. Classify it as managed if its batch, creation marker or current state
pointer identifies it. A missing, foreign or malformed batch must never fall back
to generic reflection. Original scope must match the wake's retained context refs.
The event is audit evidence; content redaction must not erase the scope needed to
validate a frozen decision. This is a second table, not a copied personal projection.

Existing generic reflection wakes retain their earlier behavior. The new batch
lookup narrows only scheduler-created reflection wakes, and always checks individual
ownership plus the original target scope. Update model-facing development guidance
to explain this distinction. A valid generic wake retains independent broad scope
in a mixed cycle, but malformed managed metadata still fails explicitly.
Establishment still needs pre-existing
owned evidence and current semantic validation. Scheduling, reviewing and retrieving
a claim never supplies another grounding anchor or strengthens it by itself.

Batch scope remains stable while wake status and cycle membership follow their
existing durable lifecycle. Diagnostics check state/wake/batch ownership, kind and
null coalesce key; immutable scope shape and bounds; cycle linkage; and deferred
materialization without reading dirty ORM objects or modifying data.

## Integration and verification

Before a new claim, validate/reconcile managed reflection after heartbeat. During
terminal completion, account for a consumed reflection batch and, if eligible,
prepare one later opportunity. All writes join the caller transaction and use the
individual/governance lock order. Reject unflushed scheduling inputs. Keep store
modules independent of runtime imports; pure timing helpers may reuse safe calendar
addition. Frozen requests are never recompiled.

Test real PostgreSQL selection, family diversity and wraparound fairness; future
maturity and cadence; owned scoping; cancelled/established targets; unchanged polls;
pause and missing configuration; terminal failure and exact-D1 recovery; rollback;
orphan rejection; historical managed scope after pointer advancement and event
content redaction; deleted historical batches; hidden live batches; mixed generic
and managed wakes; cancellation without cursor advance; and diagnostics with dirty
sessions. Acceptance must show that
merely receiving a reflection wake cannot establish an ungrounded claim, while a
properly staged, independently grounded target can be deliberately established by
the executive. No live model calls, external actions or claims of empirical identity
quality are introduced.
