# Bounded internal exploration

Internal exploration is an explicit operator allowance for a small amount of
self-directed cognition. It does not create an interest, goal, observation or
experience for the individual. The executive may choose internal work or sleep.
Normal personal-state grounding and governance still apply.

## Operator control

The reserved governance budget subtree is exactly:

```json
{"internal_exploration": {"schema_version": 1, "enabled": true}}
```

Absence means disabled. Unknown fields, non-boolean enablement and unsupported
versions are invalid. Birth validates an explicitly supplied policy before writing
anything. Existing unrelated budget keys remain unchanged by the narrow toggle.

The local authenticated CLI accepts:

```powershell
cognition admin enable_exploration --individual-id <UUID> --reason "Permit bounded internal exploration"
cognition admin disable_exploration --individual-id <UUID> --reason "Suspend exploration"
```

Each toggle records the previous/new budget policy, governance revisions, reason,
administrator and linked evidence atomically. It is allowed while paused and does
not directly finish a cycle or abandon an invocation. Invalid reserved policy
prevents fresh exploration, while ordinary cognition and exact D1 recovery remain
available. The toggle can explicitly repair an invalid reserved subtree.

## Enforced allowance

Policy 1 allows one internal turn, up to two durably recorded invocation starts,
and a 120-second deadline for starting inference. A provider call may outlast that
deadline. Failed and abandoned starts spend the same allowance. Caller limits can
reduce these caps. The minimum interval after completion or cancellation is seven
days; these are experimental resource choices, not measures of curiosity.

Ordinary due wakes have priority. Exploration is claimed alone, so an ordinary
backlog can postpone it indefinitely. The first enabled allowance may be due
immediately. Later idle time produces one overdue opportunity, without catch-up
grants. Re-enabling never resets cadence. Cancelling future pending work preserves
its existing eligibility floor and can postpone the next opportunity.

Exploration decisions cannot contain wake requests. This prevents an allowance
from scheduling an immediate ordinary cycle with a fresh turn budget. Legitimate
personal changes can still influence later heartbeat or reflection. This feature
does not bound all future cognition costs or authorize external actions.

## Persistence and recovery

Migration `0012_exploration_state` adds current operational state and historical
immutable grants. A grant binds its wake, owner, authorizing governance revision,
policy, time bounds and caps with a canonical hash. Creation/outcome envelopes
remain useful after evidence-content redaction. Mutable wake fields cannot turn
managed exploration into ordinary work or accelerate its immutable time bounds.

A mandatory `internal_exploration` control section describes the grant and frozen
effective cycle limits. Context compilation reserves its bytes before optional
content. Storage, reload and recovered decision application validate it against
durable membership. Ordinary frozen requests retain their existing representation.

A physical call becomes authorized when its invocation start commits under the
same individual/governance lock used by administration. Disabling exploration
prevents subsequent starts. A call already started may retain and apply its exact
result; global pause can defer application. A committed decision applies once
without another model call. Only the owning runtime may abandon a prior process's
unfinished start. Exhausted, expired or disabled retry-needed work settles before
context compilation or model availability checks.

Cycle completion, wake consumption and allowance accounting commit together.
Pause or missing active configuration may defer successor materialization without
erasing terminal accounting. Cancellation has its own evidence anchor and does
not invent a cognition cycle. Downgrade refuses either populated exploration table.

The CLI still advances one bounded cycle per invocation. This feature supplies no
live provider adapter, unattended service, empirical curiosity measurement or
general spending budget.
