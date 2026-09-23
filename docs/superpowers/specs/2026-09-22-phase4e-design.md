# Phase 4e: explicit bounded internal exploration

The academic architecture permits self-directed exploration when backed by an explicit
bounded allowance. Heartbeat is liveness and staged reflection is review; neither is
an exploration budget. Add an opt-in operational allowance with enforced execution
limits. This increment supplies no live provider or external action. Start
implementation only after the Phase 4d full gate and independent design review.

## Narrow operator policy

Use the existing governance `budget_policy` object's `internal_exploration` subtree:
exactly `{"schema_version": 1, "enabled": true|false}`. Absence disables exploration.
Strictly validate types and unknown fields, including explicit policy at birth before
any writes. Preserve unrelated outer budget fields. Policy 1 fixes scope to internal
work, one turn, at most two durable invocation starts, and a 120-second deadline for
starting work. A provider call can outlast that deadline; it is not a hard execution
timeout. The next allowance is at least seven days after the previous terminal outcome
or cancellation. These are experimental resource choices, not measures of curiosity or
meaningfulness. No ConfigV3 or public executive schema change is needed.

Add a narrow local-OS-authenticated admin API and CLI toggle with a required reason,
individual/admin/governance lock order, governance revision and atomic audit/event. It
audits exact old/new subtrees and governance revisions, works while paused, and edits
only this subtree while preserving unrelated budget keys. It does not expose a generic
governance editor. Birth's existing budget policy can also explicitly enable the
feature; birth still creates no imagined experience or personal interest.

Invalid policy prevents exploration starts and is diagnosed, but does not block
ordinary wake claims or exact committed-decision recovery. Unrelated governance
revision changes do not invalidate a retained allowance. Grants retain their
authorizing governance revision and exact policy snapshot.

## Explicit grant and durable cadence

Migration 0012 adds operational exploration state and immutable grant records. State
retains policy version, next eligible time, nullable managed wake and last terminal
cycle, last outcome event, deferred materialization and revision. The retained outcome
envelope supplies the actual cancellation/completion anchor; a cancellation never
invents a cycle. Grants bind wake, owner, authorizing governance revision, creation
and not-before times, scope and exact limits with a canonical hash. Retained
creation-event metadata independently identifies managed exploration even after
event-content redaction, missing grant metadata or pointer advancement. Downgrade
refuses either populated table.

The first explicitly enabled allowance may be due immediately. Bootstrap and all
ordinary due wakes take priority. The managed wake uses existing kind `routine`, null
coalesce key and a fixed purpose that permits sleep/no-op and leaves topic choice to
the executive. No objective, interest, trait or observation is fabricated. Keep at
most one live managed allowance, checking all historical grants joined to wakes rather
than trusting the current pointer alone.

Validate mutable wake timing against immutable not-before time; validate cycle start
and effective limits against that grant. Terminal completion, wake consumption, next
eligibility and retained outcome marker commit together. Failures, refusal, exhausted
attempts and process abandonment all spend attempts from the same allowance. Enabled
recovery may use a remaining attempt; terminalization consumes the allowance.
Completion is counted once even after later outcomes or content redaction. Long
silence supplies one overdue opportunity, not catch-up grants. Pause and absent active
configuration can defer successor materialization without blocking operational
terminal accounting. Re-enabling never resets cadence.

Cancelling an unclaimed allowance preserves history and advances eligibility to at
least the existing deadline or cancellation plus seven days, whichever is later.
Repeated enable/disable cannot accelerate exploration. Cancellation of future work can
postpone it; that conservative behavior is visible in evidence.

## Claim isolation and physical limits

Validate durable managed identity before dividing ordinary and exploration wakes. A
missing grant, wrong wake kind/owner or malformed creation marker cannot fall through
to an ordinary cycle with larger limits. Existing active cycles resume without
changing membership or frozen limits.

For a new cycle, claim ordinary due wakes first, excluding managed exploration. Only
if none is due, claim exactly one exploration wake by itself. An ongoing ordinary
backlog can postpone exploration indefinitely; it must not lose its normal turn budget
merely because an allowance is due. Persist effective limits as the minimum of caller
limits and policy caps: one turn, two attempts, 120 seconds, one wake. Count
invocation starts before provider calls, including failed and abandoned starts. A
committed D1 applies once without another charge or model requirement.

Apply decided D1 first, then finish exhausted/disabled retry-needed work before
context compilation/loading, model availability or preflight. Recheck caps and current
committed governance under the invocation-start lock. Otherwise a missing snapshot or
absent/exhausted adapter can strand a grant whose budget has already been spent. This
recovery ordering should apply to the shared runtime path, with regressions preserving
no-attempt preflight failures.

Exploration decisions reject any `wake_requests` atomically. Without this rule one
allowance could create an immediate self-scheduled wake and regain ordinary limits.
Existing personal operations remain available subject to all their current grounding,
ownership and lifecycle rules. A newly adopted goal or other legitimate personal
change can affect later ordinary heartbeat/reflection; this allowance bounds extra
exploration-origin cycles, not total subsequent cognition. External actions stay
blocked and the grant is never an external-action authorization.

## Disable, recovery and frozen control

The administrative toggle can cancel unclaimed work only; it never terminalizes a
durably started call or decided turn. Only the runtime with ownership can treat a
previous process's started invocation as abandoned. Authorization for a physical call
linearizes at the durable invocation-start commit under the existing
individual/governance lock. Disable prevents new exploration starts and new grants. A
call already durably started may record and apply its exact result; committed D1
likewise applies once, subject to ordinary global pause and governance. A resumed
abandoned start with no committed result does not authorize another attempt after
disable. Pending work is cancelled, and retry-needed active work terminalizes without
another call while preserving cadence.

Expose a mandatory `internal_exploration` runtime control section containing the grant
identity, internal-only scope, effective frozen limits and no-wake-request rule. Grant
data and cycle fields must agree. Validate this section against retained grant/cycle
metadata when storing/loading a request and applying recovered D1. Require exactly one
correctly categorized, type-sensitive canonical control section if and only if durable
cycle membership contains a managed allowance. Reject missing, duplicate, unexpected
or changed control, and reserve its bytes before optional context packing. Already
frozen ordinary requests and v1/v2 golden contracts remain unchanged. This is runtime
control describing enforced physics; it does not make allowance events independent
grounding evidence for inferred personal state.

## Verification

Use pure strict-policy/canonical-hash tests, real PostgreSQL migrations/stores,
read-only diagnostics and fake-clock/fresh-model acceptance. Cover absent/invalid
policy, authenticated toggle and untouched unrelated keys; ordinary-work priority;
isolated limits and all started-attempt outcomes; direct self-scheduling rejection;
no-op/personal state choices; immutable context and exact D1; disable before start,
during an authorized call and before retry; pause/config deferral; long silence;
toggle cadence; redacted/deleted history; hidden live grants and mutable identity;
atomic rollback; exhausted-budget recovery without a model; and dirty ORM isolation.

The CLI remains one bounded cycle per invocation. This does not claim unattended
service, empirical curiosity, lifelong identity quality or a general cost budget.
