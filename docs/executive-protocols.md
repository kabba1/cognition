# Versioned executive protocols

Phase 3d adds an explicit v2 executive contract for model-authored entities,
projects, relationships and relationship threads. The
[engineering gate](review/phase3d-gate.md) passes. The existing v1 public models,
serialized decisions and golden schemas remain unchanged.

## Selecting v2

Configuration schema 1 continues to select cognition protocol 1. Schema 2 requires
an explicit execution selector. In an existing deployment TOML, replace the
top-level version and add the following table, retaining the other required
configuration sections:

```toml
config_schema_version = 2

[execution]
cognition_protocol_version = 2
```

This is a configuration change fragment, not a complete deployment file. Selection
is part of the persisted behavioral configuration and its hash. For an existing
individual, activate it through the authenticated `reconcile_config` service so
the change produces durable configuration history and evidence. Editing a TOML
file alone does not replace the individual's active revision. Neither a script nor
a model response can change the selected protocol.

Migration `0008_executive_configuration` permits both configuration versions
without converting earlier history. Downgrade refuses while any v2 configuration
revision exists, including superseded revisions; it does not erase or rewrite them.
Schema migration remains an explicit operator action.

## Decisions and atomic application

`CognitionDecisionV2` reuses the seven existing personal families and adds four
required arrays. They must be present even when empty:

```json
{
  "entity_operations": [],
  "project_operations": [],
  "relationship_operations": [],
  "relationship_thread_operations": []
}
```

These fields supplement the other required v2 decision fields, including
`schema_version: 2`; the fragment is not a complete decision. A retained v1
decision is parsed as v1 without manufacturing these fields.

| New family | Supported proposals | Preserved boundary |
| --- | --- | --- |
| Entity | Create or rename | Kind and stable identity cannot change; names and kinds confer no authenticated authority. |
| Project | Create, revise or change status | Existing project transitions apply; completed or abandoned projects cannot reopen. |
| Relationship | Create or revise a narrative | Its owned entity remains fixed; replacement support belongs to the current claim. |
| Relationship thread | Create, revise, resolve or abandon | Its relationship remains fixed; terminal threads cannot reopen, and linking a commitment does not fulfill it. |

Creation uses an explicit target ID or defaults to the operation ID. Revision
requires an existing owned target; nullable optional revision fields mean
unchanged. New operations reject blank content or rationale, incompatible supplied
fields, unowned references, repeated targets and operation-ID collisions.
Relationships and threads require evidence. Entity rationale and evidence remain
in the exact decision, linked through the application event and revision history;
the entity projection itself still contains only its identity fields.

All eleven personal families join one detached validation plan and the same
application transaction. A bad operation rejects the whole decision before any
personal, focus or wake effects are applied. References must resolve to owned
objects that existed before the batch: create an entity in one committed turn
before using it to create a relationship in a later turn. The whole-decision caps
remain 64 operations and 16 wake requests. External action requests remain blocked.

Application requires the exact retained decision, its matching hash and an active
owned cycle. An interrupted application rolls back its effects together, allowing
that same retained decision to be applied on recovery. Version 2 does not alter
development-policy-1 grounding thresholds or add project events to its evidence
anchor allowlist.

## Frozen compatibility

Strict dispatch accepts only integer schema versions 1 and 2; booleans, floats,
strings and unknown versions are rejected. Structural validity is only the first
check. The following frozen combinations are supported:

| Configuration schema | Request schema / cognition protocol | Output schema | Runtime contract | Personal operation support |
| --- | --- | --- | --- | --- |
| 1 | 1 / 1 | `CognitionDecisionV1` | `2.0` | None; focus and wakes only |
| 1 | 1 / 1 | `CognitionDecisionV1` | `3.0` | Goals, commitments, beliefs and episodes |
| 1 | 1 / 1 | `CognitionDecisionV1` | `3.1` | The four above plus interests, preferences and self-model |
| 2, selecting protocol 2 | 2 / 2 | `CognitionDecisionV2` | `3.2` | All eleven personal families |

New v1 snapshots use contract 3.1; new v2 snapshots use contract 3.2. Older frozen
requests retain their earlier operation support. For example, attaching contract
3.2 to an otherwise valid v1 request is incompatible, and installing v2 handlers
does not grant those handlers to an old snapshot.

Configuration freezes when the context snapshot is committed, after the prepared
turn exists. An activated change before that snapshot may govern the turn; one
afterward cannot alter its request. Loading a snapshot checks its canonical request
representation and digest, linked configuration revision and hash, runtime-control
configuration identity, individual/cycle/turn identity, adapter/model binding and
runtime-contract metadata. Diagnostics also check these relationships. Unsupported
frozen combinations block execution without rewriting the snapshot or resampling
a retained decision.

Adapters receive the request union and return the result union. The result schema
and any successful decision schema must match the invocation's frozen cognition
protocol. A mismatch records an invocation failure rather than a successful
decision. A later active configuration does not change that requirement.

## Recovery before fresh inference

`CognitionRuntime` accepts `model=None` for recovery. It applies a retained decision
before requiring an adapter. If that decision continues the cycle, a fresh turn
can prepare its context and then return `blocked` / `model_unavailable` before an
invocation is started. Ownership, lifecycle and governance checks still apply.
Recovering an old decision does not require its original provider.

The local CLI follows the same ordering. With an active committed decision, this
command can recover without a fixture:

```text
cognition run-once --individual-id <UUID>
```

The command still checks the database schema, authenticates the local operator and
acquires runtime ownership. It postpones script loading and script-file
configuration checks until fresh inference is actually needed. A missing or invalid
script, or a changed active adapter, cannot prevent eligible committed effects
from recovering. If recovery continues to another turn, a supplied script is then
checked; a subsequent setup failure does not undo the already committed recovery.

Fresh local inference requires a matching fixture and a frozen adapter/model pair
of `script-file` / `script-file`:

```text
cognition run-once --individual-id <UUID> --script decisions.json
```

Each fixture decision has an explicit version. The next fixture is checked against
the frozen request before invocation-start. The adapter does not silently upgrade
v1 fixtures for v2 requests. A mismatch blocks with `model_request_incompatible`;
exhaustion blocks with `model_unavailable`. Neither consumes an attempt on the
pending turn. Files remain bounded to 1 MiB and 1–100 decisions;
cycle and turn IDs are bound to the request, and an omitted decision ID receives
a fresh UUID. The fixture cursor is process-local, so restarting a command starts
at the first fixture entry again. Committed decisions recover from PostgreSQL
without consuming a fixture entry.

This remains a deterministic local runner. Live provider adapters, capability
execution, background service operation and empirical long-term identity trials
remain later work. See [current status](current-status.md) and
[local operations](phase2-operations.md).
