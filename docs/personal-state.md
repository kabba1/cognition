# Grounded personal state

The first Phase 3 increment implements the existing decision v1 operations for
goals, commitments, beliefs, and episodes. PostgreSQL owns their current state;
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

Context includes bounded current commitments, goals, beliefs and recent episodes,
with object references and explicit interpretation labels. Retrieval reads state
without strengthening it. Context remains an ephemeral selection; the stored
objects and histories outlive the model request and can be recalled by a fresh
adapter with no transcript.

Current requests use runtime contract `3.0`. A frozen Phase 2 request keeps its
original contract and cannot gain these new mutation handlers during recovery.
Public protocol schema versions remain `1`; their required fields did not change.

## Supported choices

| Kind | Creation | Subsequent changes |
| --- | --- | --- |
| Goal | Active self-generated goal by default | Revise supplied content; pause, block, complete or abandon through explicit transitions |
| Commitment | Proposed by default; active can be deliberately adopted | Revise nonterminal terms; activate, fulfill, release, break or dispute as allowed by current status |
| Belief | Tentative by default | Accept with support, dispute with contradiction, withdraw, or supersede with a new proposition while preserving the old one |
| Episode | Evidence-linked summary, time span and categorical salience | Immutable; later interpretations are distinct records |

Terminal states do not silently reopen. Null fields in a revise operation mean
unchanged; v1 does not provide a clear-link/clear-date operation. Incompatible
fields reject instead of being ignored. Targets and evidence must already exist:
a sibling operation cannot introduce a reference for another operation in the
same decision. Multiple operations touching one object reject, including two
attempts to supersede the same belief.

Creation uses an explicit object ID when supplied, otherwise the operation ID;
episodes use the operation ID. A decision has at most 64 operations overall and
16 wake requests. Every applied operation has a globally unique identity.

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

Interests, preferences, values, self-model revisions and relationships still need
their distinct update rules. They remain unsupported by the executive apply path.
Project/entity executive operations require an explicit versioned extension.
The broader context/retrieval/heartbeat work is Phase 4. Live inference and external
effects are not provided by the local scripted runner.
