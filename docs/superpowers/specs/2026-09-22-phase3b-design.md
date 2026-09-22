# Interests, preferences and layered self-model

This continues the user-authorized Phase 3 implementation after the grounded
personal-state gate. The academic architecture distinguishes deliberate choices
from inferred identity: a new goal can be adopted in one decision, while one model
utterance must not establish a lasting preference or self-description.

## Approach

Keep decision v1 unchanged and implement its existing interest, preference and
self-model operations. Preserve exact decisions and atomic revision history.
Avoid a generic motivation score. Establishment requires deliberate reflective
attention and evidence from separated experiences. Retrieval never strengthens
state. These are explicit experimental rules, not scientifically validated claims
about personality. Relationships and model-authored project/entity operations
remain the next Phase 3 increment.

Alternatives considered: let any model utterance replace inferred identity
(violates the architecture); reject all initial inferred-state proposals (loses
useful candidates and makes staged reflection awkward). Instead retain candidates
and pending self-model proposals without promoting them to established state.

## Frozen experimental policy 1

New candidates cannot establish for at least 24 hours. Established preferences
cannot retire for at least seven days. Established interests first become dormant,
then cannot retire for seven days. A different inferred self-model proposal remains
pending for at least 24 hours while current content stays unchanged. Store the
eligible timestamps when staging each transition; restart does not reset them.
Record policy version 1 in evidence. A future policy change must be explicit.

Promotion is deliberate reflection when the applying cycle includes a claimed
`reflection` wake, or a `self_scheduled` wake referencing that interest/preference/
self-state. Wakes do not grant governance authority. The reflection condition is
an attention provenance requirement in addition to the ordinary ownership and
lifecycle guards. A single external observation or repeated passive retrieval
cannot satisfy it.

Grounding requires at least two distinct owned evidence events separated by 24
hours according to their recorded_at timestamps, both no later than now. Event
refs can be direct or reached through one owned episode's evidence refs. Exclude
admin/import events, cognition execution bookkeeping, and earlier interest,
preference or self-model change events: the inference cannot cite its own previous
appearance as strengthening evidence. Connector/capability observations and
model-derived goal/commitment choice events may ground a reflection. Their
epistemic meanings remain distinct; none automatically proves the inferred claim.

Eligibility is an allowlist: source_kind connector or capability; or source_kind
model with event_type personal.goal.create/revise/set_status or
personal.commitment.create/revise/set_status, linked to an actual owned
PersonalStateRevision and its AppliedOperation. No other event kind qualifies.
Episode references unwrap exactly one level to these original event IDs.

Each successful transition unions incoming references with stored references;
history never drops the prior anchor set. Initial establishment can use the union.
Re-establishing a dormant interest requires at least one qualifying event ID absent
from the prior stored anchor set, supplied by this operation. Dormant retirement
requires reflection and at least one such new anchor, in addition to the seven-day
gate. Preference retirement requires two qualifying anchors from the union plus
at least one new anchor in this operation. This prevents repeated old evidence
from masquerading as a new experience while preserving its historical relevance.

## State and schema: 0006_identity_development

New models in db/models/development.py, all individual-owned UUID IDs, UTC times,
revision >=1. Empty at birth. Reuse PersonalStateRevision and AppliedOperation.

- Interest: interest_id, individual_id, topic, summary, status
  candidate/established/dormant/retired, rationale, evidence_refs JSONB array,
  promotion_not_before, retirement_not_before nullable, created_at, updated_at,
  revision. Topic/summary are fixed by create_candidate under current v1.
- Preference: preference_id, individual_id, context, statement, status
  tentative/established/retired, rationale, evidence_refs JSONB array,
  promotion_not_before, retirement_not_before nullable, created_at, updated_at,
  revision. Context/statement fixed by create_tentative.
- SelfState: self_state_id, individual_id, layer current_identity/self_belief/
  current_value/narrative, content JSONB object nullable, evidence_refs JSONB array,
  pending_content JSONB object nullable, pending_evidence_refs JSONB array,
  pending_not_before nullable, rationale, created_at, updated_at, revision.
  Unique individual/layer. Content wraps the v1 string-or-object as {"value": ...}.
  Pending content and pending_not_before are supplied together. At least one of
  current or pending content exists. No genesis fields are copied or overwritten.

## Transitions

- create_candidate/create_tentative uses supplied ID or operation_id, scoped refs,
  nonblank text and a rationale; evidence may be empty at this explicitly tentative
  stage. Candidate creation does not claim an established trait.
- Interest establish from candidate requires age, reflection and two grounding
  anchors. From dormant it still requires reflection and at least one new grounding
  anchor, but need not meet initial establishment's two-anchor threshold. Established
  interest preservation has no periodic renewal requirement. set_dormant requires
  established state and evidence-linked rationale. Retire candidate is allowed;
  retire established is rejected until dormant; retire dormant requires its stored
  retirement time and reflective evidence. Retired cannot reopen.
- Preference establish requires tentative state, age, reflection and two anchors.
  Retire tentative is allowed; retire established requires its stored retirement
  time, reflection and two anchors. Retired cannot reopen.
- Self current_identity is an immediate deliberate presentation change; it does
  not change birth name, administrative identity, capabilities or runtime facts.
- Self narrative is an immediate subjective interpretation with at least one
  owned evidence ref. It is never labeled as a factual event.
- Self self_belief/current_value: first/different proposed content stages a pending
  proposal and timestamp, retaining current content. A matching pending proposal
  can replace current content only after eligibility, reflection and two anchors;
  union grounded references from the two proposals. An early repeat may add distinct
  references but cannot reset the deadline or promote state. Replacing pending
  content starts a new deadline; the earlier proposal remains in revision history.
  Repeating identical current content with no pending change is a no-op rejection.

Reject incompatible unused fields on noncreate interest/preference operations,
foreign references, repeated targets/layers within one decision, reused operation
IDs and all unsupported action requests. Existing four-family operations may share
a decision with these operations, but the entire batch must validate before writes.
All applies require exact persisted D1 and shared individual locking. Current/pending
state, evidence/revisions and wake effects are one transaction.

Require nonblank rationale for development operations and nonblank strings/nonempty
objects for self-model proposed content. Normalize time before comparisons. An
episode wrapper around an old anchor does not make its underlying event new.
No grounding scan follows arbitrary recursive belief/reference graphs.

## Context and inspection

Runtime contract advances to 3.1; older frozen snapshots cannot gain these handlers.
Frozen 3.0 retains its four personal families; frozen 2.0 retains focus/wakes only.
Support is checked per family and contract, not equality to the newest version.
One bounded section per object preserves current vs candidate/dormant/pending labels.
Expose the relevant eligible timestamps and policy explanations so the executive
can plan reflection without futile retries. Retrieval is read-only. Inferred
self-state is separate from immutable genesis and runtime-derived current facts.
Integrity diagnostics extend to owned references, revision chains and staged times.

## Verification

Test one-utterance resistance; passive exposure and retrieval cannot promote;
wrong wake kinds; duplicate or insufficiently separated evidence; own-change-event
feedback exclusion; promotion with qualifying reflection; all transitions and
terminal states; seven-day retirement gate; pending self changes preserve current
content; repeated pending proposals do not postpone eligibility; genesis unchanged;
mixed-family all-or-nothing rejection; exact D1 recovery at personal write boundaries.
Run independent review and complete PostgreSQL/static gates before proceeding.
