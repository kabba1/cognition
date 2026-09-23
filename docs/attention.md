# Deterministic attention and urgent recall

Phase 4a builds context from owned durable records before inference. It adds
explicit-reference recall and urgent commitments to the earlier recent-state
pools. Selection runs in the same coherent transaction as the context snapshot;
retrieval never changes a belief, trait, relationship, commitment or revision.
The [engineering gate](review/phase4a-gate.md) passes.

## Candidate policy 1

These are inspectable engineering defaults, not a learned relevance score:

| Pool | Bound | Selection |
| --- | --- | --- |
| Urgent commitments | Eight detailed records plus a ninth-row overflow flag | Active/disputed obligations due within 24 hours, earliest due first, stable ID for ties. |
| Direct personal references | 32 unique lookups | Wake refs first, then focus refs; deterministic wake/ref order. |
| Linked personal references | 16 additional unique lookups | One explicit parent hop from direct records. |
| Recent personal state | Existing per-family limits: eight, or four self layers | Existing eligibility and active/established priorities, recency and stable identity. |
| Evidence | Existing bounded causal, referenced and recent pools | Wake causes and references precede ordinary recent evidence; protected content remains omitted. |

Direct references can recall terminal records, such as a completed goal or resolved
thread. Their status is rendered honestly; retrieving them does not reopen them.
Missing or foreign personal references remain pointers and expose no foreign
record content. Nonpersonal refs are handled by their existing context paths.

Parent expansion follows goal to project, belief to subject entity, commitment to
counterparty entity, relationship to entity, and thread to relationship/commitment.
It does not recursively follow evidence, memories or the social graph. The fixed
relationship projection includes its owned entity as one atomic section; both
rendered refs and all its bytes are accounted for. A parent ID inside other
content remains a pointer unless the parent content is actually rendered.

More than eight urgent obligations set `urgent_scan_truncated`. This says the
urgent candidate pool has more rows, not that every extra obligation is absent
from context: one may also be retrieved by a direct reference or the recent pool.
The executive can resolve a selected obligation and see later obligations on the
next turn. Selection itself never changes commitment status to advance the pool.

## Packing and explanation

Invariant runtime control, governance, genesis, present time, wakes and focus
remain mandatory. The bounded attention summary and selected urgent details are
also mandatory. If these cannot fit, the existing `context_budget` failure path
ends the cycle before a provider attempt; an operator must address the budget or
oversized mandatory content before expecting that context to run.

Causal evidence and direct personal/evidence references precede linked records
and ordinary recent pools. Recent pools take turns by family so one family cannot
consume every opportunity. Each optional object is packed whole or omitted, and
the compiler continues trying smaller objects after an oversized one. Selection
deduplicates primary records; equally ranked projections have a canonical tie-break.
Time and event ordering use exact integers, including PostgreSQL BIGINT sequences.

`attention_summary` records policy version, limits, unique candidate count,
selected/budget-omitted counts and source truncation/unresolved-reference counts.
Source truncation counts describe bounded lookups, not known omitted content.
Summary bytes are reserved before packing and the final request is checked against
the configured UTF-8 byte estimate plus framing reserve.

Snapshots retain only actually rendered refs in `selected_refs`. Reasons distinguish
`urgent_commitment`, `wake_reference`, `focus_reference`, `linked_reference`,
`recent_personal`, `wake_cause`, `context_reference` and `recent_evidence`.
Embedded pointers do not add entries merely by being mentioned. Selection records
what was available to the model without claiming that the model used it correctly.

This policy applies only when a fresh context snapshot is compiled. Recovery
preserves already frozen requests, even when records, wake refs, configuration or
the active model have changed. Executive protocol v1/v2 and their supported
semantic operation families are unchanged by attention policy 1.

Lexical full-text retrieval, adaptive heartbeat, reflection/exploration scheduling,
connector perception and live-model recall evaluation remain separate work.
