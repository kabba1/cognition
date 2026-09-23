# Draft: explicit executive extension for social and project state

This is a forward design, not implemented functionality or an accepted gate.
Review it against the completed relationship substrate before implementation.
The purpose is to let cognition deliberately create/revise entities, projects,
relationships and threads without silently widening frozen v1 public schemas.

## Version selection and compatibility

Introduce CognitionDecisionV2, ModelRequestV2 and ModelResultV2 in separate modules.
Reuse unchanged operation models from v1, but leave v1 public classes and golden
schemas unchanged. V2 decisions add required explicit entity_operations,
project_operations, relationship_operations and relationship_thread_operations
arrays. Do not silently upgrade a retained v1 decision or synthesize its new fields.
V2 results carry only V2 decisions; V1 results continue to carry only V1 decisions.

Add explicit configuration schema 2 and its persisted behavioral subset with an
execution.cognition_protocol_version=2 selection. Existing configuration schema 1
continues selecting cognition protocol 1. Preserve all other behavior sections.
Selection changes must pass the existing authenticated/config-history path and
produce configuration evidence. Do not hide protocol selection in inference
preferences, process flags or model-provider state.
An explicit Alembic migration must widen RuntimeConfigRevision's current
config_schema_version=1 constraint. Preserve existing revision JSON and history
exactly; refuse downgrade while v2 revisions exist rather than deleting/converting
them. Restore the v1-only constraint only after proving no incompatible records.

Runtime contract 3.2 accompanies cognition protocol 2; protocol 1 retains contract
3.1. Frozen requests retain their configuration/protocol/contract and parser.
Central strict dispatch checks an integer schema version, rejecting bool/float/
string coercions and unknown versions. Loading exact retained JSON selects a
parser without rewriting its bytes or hash. Current adapters accept a request
union and return a result union; the runtime requires result/decision protocol to
match that invocation's frozen request before retaining a successful decision.
Configuration freezes at context-snapshot commit, not the earlier prepared-turn
creation. Changes before that snapshot can govern the turn; changes afterward
cannot. Maintain an allowed-tuple registry across configuration selection, request
schema/cognition_protocol_version/output_schema, runtime contract, result schema
and decision schema. A individually valid V1 request labeled contract 3.2 is not a
valid tuple. Cross-check the snapshot's denormalized contract/config linkage when
loading, applying and diagnosing it. Unsupported/mismatched frozen versions block
and report incompatibility without rewriting snapshots or resampling committed D1.

## New proposal families

EntityOperation: create or revise; operation_id, entity_id nullable, kind nullable,
display_name nullable, evidence_refs, rationale. Creation requires nonblank kind
and name; revise requires an existing target and permits display-name changes only.
Entity kind and stable ID do not change. No merge, authenticated identifier binding
or authority operation exists. Rationale/evidence are retained in exact decision
history even though the existing entity projection contains only identity fields.

ProjectOperation: create, revise or set_status; operation_id, project_id nullable,
title nullable, desired_state nullable, requested_status nullable, evidence_refs,
rationale. Use the existing project transition matrix and terminal preservation.
Creation may deliberately adopt a project immediately; inferred-state delays do
not apply to volitional choices.

RelationshipOperation: create or revise; operation_id, relationship_id nullable,
entity_id nullable, narrative nullable, evidence_refs, rationale. Creation requires
an existing owned entity and narrative. Revision cannot reparent the relationship.
Evidence belongs to the current narrative; previous support remains in history.

RelationshipThreadOperation: create, revise or set_status; operation_id,
thread_id nullable, relationship_id nullable, title nullable, summary nullable,
commitment_id nullable, requested_status nullable, evidence_refs, rationale.
Match the substrate's open/resolved/abandoned rules and immutable relationship.
Null revise fields mean unchanged. Linking a commitment never changes its status.

All IDs default to operation_id only on creation; otherwise require a target.
Reject unused/incompatible supplied fields, blank content/rationale, unowned refs,
repeated targets and global operation-ID collisions. Sibling operations cannot
introduce a reference for one another: create an entity in one committed turn
before referring to it in a later turn. Whole-decision operation/wake limits remain.

## Application and tooling

Extend the detached plan and single personal apply loop; no new family writes
before every family validates. Apply still requires exact retained D1 on an active
owned cycle. Reuse the same history/event machinery as trusted substrate APIs.
Old frozen contracts cannot acquire these handlers. References/context already
understand the corresponding personal records from Phase 3c.

Teach ScriptFileModel and deterministic scripted adapters both explicit versions.
The CLI checks templates against the contract needed for fresh inference, including
a frozen pending turn. Committed D1 recovery precedes requirements for a script or
the current active model configuration; it needs the authenticated operator, schema
and ownership guards but no provider. If recovered D1 continues to a new turn,
fresh inference can then block for missing/incompatible adapter, protocol or script.
The existing CLI currently checks its script/current adapter before recovery; this
extension must deliberately replace that ordering and regression-test the boundary.
Keep fixture size/count bounds and credential-free local execution unchanged.
Integrity diagnostics parse either known version and preserve read-only semantics.

## Required evidence before release

Golden round trips and schema snapshots for all new public contracts; unchanged v1
goldens; strict version rejection; explicit configuration history; v1/v2 request and
result mismatch rejection; model-free recovery of committed v1 and v2 D1; mixed
eleven-family all-or-nothing application; entity/project/social context on a fresh
adapter; foreign-link and terminal-state rejection; no social authority escalation;
pause/ownership loss and interrupted-write recovery. Review adapter compatibility
and configuration migration before implementation. No live provider or external
action execution is part of this extension.
