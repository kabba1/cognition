# Relationship substrate and social context

The next bounded Phase 3 increment implements relationships and open threads from
the academic architecture. A relationship assembles stable entity identity,
evidence, entity-linked beliefs, commitments and a replaceable subjective narrative.
It has no universal trust, friendship or emotion score and grants no authority.

## Boundary

Build canonical persistence, trusted internal mutation APIs, bounded read context
and diagnostics first. Decision v1 has no entity/project/relationship mutation
operations, so it remains unchanged. The next increment must introduce an explicit
versioned executive extension before claiming model-authored social/project state.
Existing decisions can cite these owned records and their underlying entities.
Internal APIs join the caller's transaction, just like existing entity/project APIs;
they are not an unauthenticated social or administration endpoint.

## Schema: 0007_relationships

Relationship: relationship_id UUID PK, individual_id FK, entity_id FK, narrative
nonblank text, rationale nonblank text, evidence_refs JSONB array, created_at and
updated_at timestamptz, revision bigint >=1. Unique (individual_id,entity_id).
One relationship concerns one owned entity. Narrative is an interpretation with
at least one owned evidence reference; it is not an interaction log or a statement
of verified truth. Replacing the narrative attaches the new claim's supplied
support; previous content/support remain in PersonalStateRevision.
The entity_id is immutable; revising a narrative cannot reparent the relationship.

RelationshipThread: thread_id UUID PK, individual_id FK, relationship_id FK,
title and summary nonblank text, status open/resolved/abandoned, optional
commitment_id FK, rationale nonblank text, evidence_refs JSONB array, created_at and
updated_at timestamptz, revision bigint >=1. Threads start open, can revise while
open, then resolve or abandon. Terminal threads cannot reopen or be revised.
A follow-up is a distinct thread. Linking an owned commitment does not mark it
fulfilled or change its terms. Evidence is required on creation and revision.
All relationship/entity/commitment links must belong to the same individual.
The relationship_id is immutable; revising a thread cannot move its history to
another relationship. The commitment link may be changed to another owned
commitment while open; null means unchanged in this initial internal API.

Birth leaves both tables empty. Reuse exact before/after histories and personal.*
events. Database foreign keys constrain existence; stores and diagnostics enforce
cross-table ownership, as with the existing personal state tables.

## APIs and atomicity

New stores/relationships.py provides create_relationship, revise_relationship,
create_relationship_thread, revise_relationship_thread. Keyword arguments follow
existing personal APIs; optional IDs on create, null revise fields mean unchanged.
Require nonblank rationale/content, owned evidence, valid transitions and real
changes. Reject foreign/missing links and conflicting unflushed ORM state before
writing. Acquire the individual lock, refresh existing rows, and share the current
revision/event writer without a second commit or independent transaction.

References use kinds relationship and relationship_thread. No entity merging,
authenticated connector identifier binding or model-derived authority is added.
Entity identifiers and goal dependency edges remain explicit future work.

## Context

Add at most eight owned relationships and eight open threads as separate category
relationship sections, ordered deterministically by updated_at descending and ID.
Include the owned entity's display identity alongside the relationship interpretation,
references to the relationship/entity, and labels that
social interpretation does not grant authority. Existing beliefs and commitments
already retain entity links; do not duplicate the entire event history here.
Thread sections render the thread itself and reference that thread only. Linked
relationship/commitment IDs remain nested pointers unless their own content was
also rendered. Top-level refs must not falsely report retrieval of objects whose
independent sections may have been dropped by the context budget.
Add at most eight active/paused/blocked projects and eight entities as separate
bounded sections so the executive can use existing v1 project/entity references.
The context compiler's global budget still determines which sections fit.
Read operations use stored column values without autoflush or implicit mutation.

## Verification

Schema tests cover upgrade/downgrade, metadata drift, constraints and empty birth.
Real PostgreSQL store tests cover ownership, narrative replacement support/history,
thread transitions, terminal preservation, no-op rejection, transaction rollback,
stale-session refresh and conflicting unflushed writes. Runtime tests use seeded
relationships to verify fresh-model bounded context and read-only reference use;
they must not imply that v1 can create a relationship. Extend integrity checks to
both models, thread links, evidence and exact history. Independently review and run
the complete static/PostgreSQL gate before publishing this increment.
