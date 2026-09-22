# Grounded personal state: Phase 3 first increment

The user authorized continuing through the academic architecture while away and
filling routine implementation gaps. This architectural increment follows the
Phase 2 recovery gate; it does not claim completion of the entire personal layer.

## Intent and boundaries

Persist beliefs, episodes, goals and commitments as the individual's revisable
interpretations and choices. Their evidence links establish provenance, not truth.
Birth still creates no personal memories. Immutable genesis, runtime facts and
administrative authority remain outside the model mutation interface.

Keep the existing decision v1 shape. Implement its four existing operation families
through whole-decision validation followed by atomic application. Interest,
preference, self-model and action families remain rejected until the next increment
implements their distinct inertia and authority rules. Projects and entities have
minimal durable store APIs supporting their existing references; v1 has no model
operation to invent or alter them. Later work must explicitly extend that boundary.

Alternative considered: a generic personal-state JSON document. Rejected because
queryable statuses, ownership, evidence and transition constraints deserve explicit
relational state. Alternative considered: full event replay. Rejected in favor of
current projections plus append-oriented revisions, matching the academic design.

## Schema contract: migration 0005_personal_state

All IDs are UUID, times timezone-aware UTC, mutable rows have BIGINT revision >=1.
All rows belong to an individual via FK. Named checks constrain statuses and JSON
shapes. Tables are initially empty; no migration fabricates memories. Source file
`db/models/personal.py` exports:

- `Entity` / entities: entity_id PK, individual_id FK, kind text, display_name text,
  created_at, updated_at, revision. Entity identity conveys no authentication.
- `Project` / projects: project_id PK, individual_id FK, title, desired_state,
  status (active/paused/blocked/completed/abandoned), rationale, evidence_refs JSONB
  array, created_at, updated_at, revision.
- `Goal` / goals: goal_id PK, individual_id FK, project_id nullable FK projects,
  title, desired_state, status (same as project), origin (all GoalOrigin v1 values),
  rationale, evidence_refs JSONB array, created_at, updated_at, revision.
- `Commitment` / commitments: commitment_id PK, individual_id FK,
  counterparty_entity_id nullable FK entities, title, terms, status (all v1 values),
  due_at nullable, rationale, evidence_refs JSONB array, created_at, updated_at, revision.
- `Belief` / beliefs: belief_id PK, individual_id FK, proposition,
  subject_entity_id nullable FK entities, topic nullable text, status (all v1 values),
  supporting_evidence and contradicting_evidence JSONB arrays,
  supersedes_belief_id nullable self FK, rationale, created_at, updated_at, revision.
- `Episode` / episodes: episode_id PK, individual_id FK, summary, starts_at and
  ends_at nullable, evidence_refs JSONB array, entity_refs/project_refs JSONB UUID
  string arrays, salience_factors JSONB string array, created_at, revision=1.
  Check ends_at >= starts_at when both supplied. Episodes are immutable interpretations.
- `PersonalStateRevision` / personal_state_revisions: revision_id PK,
  individual_id FK, object_kind text, object_id UUID, revision BIGINT >=1,
  operation_id nullable unique FK applied_operations, turn_id nullable FK cognition_turns,
  event_id nonnull FK events, before_json nullable JSONB object, after_json JSONB
  object, created_at. Unique(object_kind,object_id,revision). Entity/project store
  changes have no executive operation/turn; model changes carry both. Every change
  appends an evidence event and exact before/after snapshot in the same transaction.

## Transition and evidence rules

Validate every reference against the same individual. Kinds supported here include
existing event/wake/cycle/individual/governance plus entity/project/goal/commitment/
belief/episode. Personal interpretation refs do not become administrative authority.
For this increment targets and evidence must exist before the decision; operations
cannot reference rows created by sibling operations. Reject multiple operations
targeting the same object in one decision, rather than relying on array order.

Create IDs use supplied object ID if present, otherwise operation_id. Episode ID
is operation_id. Collision with an existing object of that kind is a semantic
rejection, including foreign-owned objects. Every operation ID is globally unique
across applied operation families, including wakes. Cap total operations at 64.

- Goals: create defaults active and self_generated. Creating in terminal status
  is invalid. revise may change supplied title/desired_state/project and records
  rationale/evidence; status changes use set_status. Active/paused/blocked may
  move between these or to completed/abandoned. Terminal goals cannot reopen.
  Self-generated volition may have no evidence; claims of external origins still
  remain model rationale, not proof of an external request.
- Commitments: create defaults proposed; active is allowed as explicit adoption.
  Proposed can activate/release; active can fulfill/release/break/dispute; disputed
  can activate/release/break/fulfill. Fulfilled/released/broken are terminal.
  revise changes supplied title/terms/counterparty/due_at on nonterminal rows.
  A model status is an interpretation of a promise, not an external action receipt.
- Beliefs: create defaults tentative. No belief may enter accepted without at least
  one supporting evidence ref. Disputed requires contradicting evidence. Evidence
  refs must resolve; evidence links do not establish their claims as true.
  set_status can move tentative/accepted/disputed among those states or withdraw;
  superseded/withdrawn are terminal. supersede creates a new proposition and marks
  the old belief superseded in one transaction, preserving both and their histories.
  It requires supporting or contradicting evidence and an owned nonterminal target.
  Never rewrite the old proposition. set_status preserves existing evidence and
  unions new refs. Create/supersede cannot start in superseded/withdrawn state.
- Episodes: require at least one existing evidence ref and nonfuture spans.
  Store summary as model interpretation with links and salience categories; no
  invented emotion scores. Entity/project references must exist and be owned.

Nullable fields in revise mean unchanged; clearing links/dates requires an explicit
future contract, rather than silently treating absent and clear as the same action.
No-op status transitions reject. Store APIs validate detached Pydantic inputs before
mutations. SQL writes join caller transaction; unexpected DB failures still roll back.

All public mutation paths, including standalone entity/project stores, acquire the
individual FOR UPDATE first. Hold it through validation, populate-existing reads,
before/after snapshots and caller commit. This matches runtime/admin lock order and
serializes personal mutations without lost revisions. Validation/apply in separate
transactions is unsupported; apply revalidates under the same lock before any write.
Refuse unflushed conflicting ORM state rather than silently overwriting a caller's
pending updates. No automatic retries of arbitrary caller transactions.

Reject incompatible fields instead of silently ignoring them: goal/commitment revise
cannot set requested_status, and set_status cannot alter content/link/date fields.
Goal origin is fixed after create. Belief create requires supersedes_belief_id=null;
set_status cannot alter proposition/subject/topic or supersession link. Supersede
treats both its new ID and old superseded ID as touched targets for conflict checks.
The supersession event explicitly identifies operation_id and both belief IDs so
both history records remain connected to the same model operation.

## Integration and context

`stores/personal.py` exposes validate_personal_operations(session, individual_id,
decision, now)->tuple[str,...], apply_personal_operations(session,individual_id,
decision,now)->None, personal_reference_exists(session,individual_id,ref)->bool,
and bounded `personal_context_sections(session,individual_id)->list[ContextSection]`.
Application follows successful whole-decision validation. Each operation logs
AppliedOperation before its history FK, updates current projection and appends
personal-state evidence/revision. No standalone commit. Belief supersession records
two revisions sharing the operation; therefore only the new belief revision carries
operation_id, the old revision retains turn_id/event linkage.

The public apply boundary additionally requires an active owned cycle and a decided
turn whose exact stored JSON, identity, disposition and digest match the supplied
decision. Prospective validation may inspect an uncommitted proposal; application
cannot attach arbitrary effects to a merely existing turn. Personal context emits
one section per object so an oversized record does not discard its entire family.

Root extends policy support for these four families and global operation/ref checks;
runtime apply checks personal validation before any focus/wake writes. Context
compiler accepts optional personal sections and packs them before optional recent
evidence under the existing budget. Sections label interpretations as model-derived,
include status and references, and read without mutation. Bounded slices prioritize
active commitments/goals, current beliefs and recent episodes. Phase 4 will add FTS
and richer relevance; retrieval itself never increases personal-state strength.

## Verification

Real PostgreSQL constraints, migration drift/up/down, empty birth, lifecycle matrices,
foreign references, operation collisions, unsupported family atomic rejection,
belief supersession with both histories, episode provenance, rollback after partial
writes and recovery of exact committed D1. Restart/continuation context must expose
persisted personal state without using a model conversation transcript. Independent
review plus full tests/Ruff/strict mypy before recording this increment's gate.
