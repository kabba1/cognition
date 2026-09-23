# Grounded personal state

Phase 3 implements the existing decision v1 operations for goals, commitments,
beliefs, episodes, interests, preferences and layered self-state. PostgreSQL owns their current state;
each change also writes a personal-state revision and an evidence event in the
same transaction. Genesis and governance remain separate and unchanged.

These records describe choices and interpretations. An accepted belief records
what the individual accepts, with supporting references; it is not certification
that an external claim is true. An episode is a retained, lossy interpretation
linked to evidence. A commitment marked fulfilled is a model interpretation, not
a fabricated action receipt or independent verification of an external effect.

## Execution and recovery

The runtime first retains the exact decision, then validates the entire proposal
against current owned state. A bad reference, unsupported operation, conflicting
target, reused operation ID, or forbidden transition rejects the whole decision.
It cannot partially change focus, create a goal, or schedule a wake. Application,
revision history, operation tracking and wake consumption commit together. Crash
recovery reuses the saved proposal, never regenerating an already committed decision.

Context includes bounded current commitments, goals, beliefs, recent episodes,
interests, preferences and layered self-state,
with object references and explicit interpretation labels. Retrieval reads state
without strengthening it. Context remains an ephemeral selection; the stored
objects and histories outlive the model request and can be recalled by a fresh
adapter with no transcript.

Current requests use runtime contract `3.1`. Frozen `3.0` requests retain the first
four personal families; frozen Phase 2 requests retain focus and wake operations.
An older request cannot gain new mutation handlers during recovery.
Public protocol schema versions remain `1`; their required fields did not change.

## Supported choices

| Kind | Creation | Subsequent changes |
| --- | --- | --- |
| Goal | Active self-generated goal by default | Revise supplied content; pause, block, complete or abandon through explicit transitions |
| Commitment | Proposed by default; active can be deliberately adopted | Revise nonterminal terms; activate, fulfill, release, break or dispute as allowed by current status |
| Belief | Tentative by default | Accept with support, dispute with contradiction, withdraw, or supersede with a new proposition while preserving the old one |
| Episode | Evidence-linked summary, time span and categorical salience | Immutable; later interpretations are distinct records |
| Interest | Candidate with a fixed topic and summary | Establish through reflection; become dormant; re-establish or retire with new grounding |
| Preference | Tentative contextual statement | Establish through reflection; retire through a separate delayed transition |
| Self-state | Deliberate presentation, subjective narrative, or pending inferred belief/value | Immediate presentation/narrative revision; staged and grounded inferred-state revision |

Terminal states do not silently reopen. Null fields in a revise operation mean
unchanged; v1 does not provide a clear-link/clear-date operation. Incompatible
fields reject instead of being ignored. Targets and evidence must already exist:
a sibling operation cannot introduce a reference for another operation in the
same decision. Multiple operations touching one object reject, including two
attempts to supersede the same belief.

Creation uses an explicit object ID when supplied, otherwise the operation ID;
episodes use the operation ID. A decision has at most 64 operations overall and
16 wake requests. Every applied operation has a globally unique identity.

## Development through reflection

Experimental development policy `1` separates a proposal from an established
interest, preference, self-belief or current value. A candidate or inferred proposal
must wait at least 24 hours. Establishment requires a reflection wake (or a
self-scheduled wake referencing that object) and two distinct qualifying evidence
events recorded at least 24 hours apart. Eligibility timestamps are durable:
restarting the runtime or retrieving the record does not advance or reset them.
These thresholds are engineering policy, not validated measures of personality.

Qualifying anchors are owned connector/capability events, or model-derived goal
and commitment choice events linked to actual applied operations and revisions.
One episode reference may expose its original event anchors. Repeated references
to the same event count once. Administration, imports, bookkeeping and the earlier
interest/preference/self-state change itself cannot provide grounding. Evidence
provenance still does not prove the inferred claim.

An established interest must become dormant before retirement; retirement then
requires seven days, reflection and a new qualifying anchor. Re-establishment of
a dormant interest also requires reflection and a new anchor. An established
preference can retire after seven days with reflection, two separated anchors and
at least one new anchor. Tentative candidates can be retired without claiming they
were ever established. Retired records cannot reopen.

The self-model has four layers. `current_identity` changes deliberate presentation
without touching immutable genesis or governance. `narrative` is an immediate
subjective interpretation with an owned evidence reference. `self_belief` and
`current_value` retain current content while a replacement is pending. Repeating
the same pending claim can add evidence without postponing its deadline; confirming
it after the deadline still requires reflection and grounding. A different pending
claim starts its own deadline and explicitly supplied evidence, while the abandoned
claim remains in history. Replacing current content also replaces its attached
support with the new claim's evidence; prior support remains in history. Context
labels current and pending content separately.

Each individual has at most one current row per self layer, with successive content
in revision history. The first proposal's operation ID identifies that layer row.
Multiple proposals to the same layer in one decision reject the entire decision.

## Entities, projects and provenance

Entities have stable identity and a display description, but no implicit login or
administrative authority. Projects organize related goals. Internal store APIs
`create_entity`, `create_project`, and `revise_project` validate ownership, lock the
individual and join the caller's transaction. They retain evidence and before/after
history. The decision v1 contract does not yet expose entity or project mutation;
model operations may only reference existing entities/projects.

All personal mutation paths serialize on the individual row. Stores refresh stale
rows after acquiring the lock and refuse conflicting unflushed caller changes.
Read-only integrity diagnostics check owned references, revision chains, current
projections and their evidence/operation links without flushing or repairing state.

## Remaining Phase 3 work

The [relationship substrate](relationships.md) now provides trusted internal APIs
and bounded social context. Model-authored project/entity/relationship operations
still require an explicit versioned extension.
The broader context/retrieval/heartbeat work is Phase 4. Live inference and external
effects are not provided by the local scripted runner.
