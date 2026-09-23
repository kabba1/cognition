# Phase 4a: deliberate attention and urgent recall

The academic specification calls for deterministic, inspectable candidate selection
before lexical search and later adaptive scheduling. The concrete current defect is
that eight recent objects per family can displace an older goal explicitly named
by a wake, or a commitment due soon. Implement reference and deadline selection
before adding FTS, heartbeat or a new decision protocol.

## Scope and boundaries

Keep PostgreSQL canonical and context reads in the existing coherent transaction.
Retrieval never changes personal state, revision history, grounding or authority.
No new tables, schema migration, configuration version or public decision fields.
Frozen requests remain exact; fresh contexts record attention policy version 1.
This is an inspectable engineering policy, not a validated relevance metric.

Entity identifiers and goal dependencies remain explicit backlog. Identifier
binding belongs with connector provenance; dependency edges need an explicit
semantic operation extension. Neither blocks reference retrieval over existing
owned entity/project/goal/commitment/relationship links.

## Candidate construction

Introduce a detached `AttentionCandidate(section, reason, mandatory=False)` and
`PersonalAttention(candidates, omissions)` in `domain/attention.py`, which depends
only on protocol types. Stores must not import the runtime package.
The store gathers candidates with column snapshots and no autoflush; it must not
read dirty identity-map objects or flush unrelated pending caller edits.

Collect distinct wake context references followed by focus references, preserving
deterministic wake order and sorting refs within each source. Bound the combined
direct lookup to 32 unique supported personal refs. Use owned rows only, including
terminal records when directly named, so a reference can recall a completed goal.
Unsupported/missing/foreign refs remain pointers and never enter selected_refs.
Record counts for lookup truncation and unresolved refs without exposing foreign
record content. A section's refs still identify only content actually rendered.

Gather at most eight active or disputed commitments due on or before now plus
24 hours, ordered by due time then stable ID. These are mandatory candidates.
Query a ninth row to detect overflow. If it exists, add a mandatory compact notice
that at least nine urgent obligations exist and the urgent pool was truncated
(`urgent_scan_truncated`). This does not claim final omission: another urgent
record may also arrive through an explicit reference. Recompute on each new turn
so resolving some obligations exposes
the next ones. Do not deadlock all cognition because a legitimate backlog exceeds
eight. These limits and the 24-hour horizon are policy-1 choices in the snapshot.

Expand at most one explicit parent hop from direct candidates, bounded to 16
additional unique owned refs: goal -> project; belief -> subject entity;
commitment -> entity; relationship -> entity; thread -> relationship and linked
commitment. Do not recursively traverse evidence, episodes, pending inferences or
the entire social graph. Directly referenced terminal parents may be rendered.
The existing relationship projection atomically includes its owned entity. This
fixed projection is an explicit exception to parent-hop counting, not permission
to traverse onward: its complete bytes and both refs count as rendered content.
Ordinary recent candidates retain existing per-family bounds and projections.
Deduplicate by the primary object ref, preferring mandatory, then direct, then
linked, then recent. Preserve the development policy guidance and relationship
entity rendering in all retrieval paths.

## Packing and explanation

Pack invariant control/identity/present/wakes/focus and mandatory urgent commitments
first. Any mandatory byte-budget overflow follows the existing no-inference
context-budget failure path. Then pack causal evidence and directly referenced
personal/evidence candidates, one-hop linked candidates, and ordinary recent pools.
Within ordinary personal pools use deterministic round-robin by family to avoid
the current family order consuming all remaining space. Every optional object is
atomic: skip a large object and keep trying smaller ones.

Keep evidence sanitization unchanged. A causal event may be too large and must be
reported as omitted rather than promoted to unsanitized control. Foreign refs and
embedded source instructions cannot grant authority. Parent IDs inside content
do not claim parent retrieval unless the parent content is separately rendered.

Persist reasons such as urgent_commitment, wake_reference, focus_reference,
linked_reference and recent_personal for selected refs; preserve existing evidence
reasons. A bounded attention summary records candidate counts, source omissions
and budget omissions. Candidate counters describe unique primary objects after
deduplication; input-reference omissions are separate counts. Reserve summary
space using maximum values of bounded counters before packing; render actual
counts once and verify the final estimate. Selected refs/reasons describe final
rendered content only. Summary accounting must never exceed the byte budget.
The snapshot identifies policy 1 and its constants; executive contract versions
continue to describe supported semantic operations, independently of retrieval.

## Verification

An old referenced goal survives more than eight newer goals; a terminal referenced
goal is recalled; due commitments precede unrelated recent activity; foreign and
missing refs expose no content; one-hop expansion stops at its cap and never
recurses. Nine or more urgent commitments produce a visible bounded overflow
notice and resolving selected obligations exposes further ones. A ninth urgent
record retrieved directly does not produce a false final-omission claim. Mandatory byte
overflow fails before a provider call.
Optional oversized objects do not hide smaller ones. Shuffled source order and
equal timestamps produce deterministic output, selected refs and reasons.

Real PostgreSQL tests demonstrate dirty-ORM isolation, no revision/event changes
from retrieval, fresh-model recall without a transcript, and unchanged frozen
requests after state/configuration changes. Existing v1/v2 recovery and authority
tests remain green. Independently review store selection and compiler packing,
then run the complete PostgreSQL and static gates before publication.
