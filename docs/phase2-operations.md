# Phase 2 local cognition smoke run

This guide describes the local runner introduced in Phase 2, including its current
recovery behavior. Current schema head is `0008_executive_configuration`; run
`alembic upgrade head` explicitly and consult [personal state](personal-state.md)
and [executive protocols](executive-protocols.md) for supported operation families.

Phase 2 advances one recoverable cognition cycle through persisted context,
invocation, decision, and atomic application. The `run-once` command consumes
local JSON decision fixtures. **It does not call a live language model.** Live
provider adapters remain a later deliverable. This command makes no network
provider calls, executes no code from its fixture, and starts no daemon.

## Prerequisites

1. Install the application and explicitly migrate the PostgreSQL database to
   the current head using the existing Alembic deployment workflow. The command
   checks the schema and never migrates it automatically.
2. Set `COGNITION_DATABASE_URL` for the intended database. Do not put credentials
   in a fixture or commit them to configuration history.
3. Birth the individual through the existing birth API, with the current process
   identity from `cognition.cli.commands.admin.local_principal()` registered as an
   active administrator. Identity comes from the OS token, not username environment
   variables or CLI identity overrides.
4. Use an explicitly activated behavior configuration whose `model.adapter` and
   `model.requested_model` are both `script-file`. At birth these are the `[model]`
   entries in the supplied configuration. For an existing smoke-test individual,
   an authorized local operator can use the existing `reconcile_config` service
   to record a new configuration revision and its evidence. `run-once` never
   rewrites configuration for you.

Keep the individual active and inference permitted to execute a due wake. Birth
creates a bootstrap wake. The runtime preserves the external-action block and
does not grant or dispatch external capabilities.

## Fixture format

Save this UTF-8 JSON as `sleep.json`:

```json
[
  {
    "schema_version": 1,
    "disposition": "sleep",
    "rationale_summary": "Local fixture smoke run.",
    "current_focus": {"summary": "Inspect the smoke-run result.", "refs": []},
    "goal_operations": [],
    "commitment_operations": [],
    "belief_operations": [],
    "episode_operations": [],
    "interest_operations": [],
    "preference_operations": [],
    "self_model_operations": [],
    "action_requests": [],
    "wake_requests": []
  }
]
```

The file is an array of 1–100 decision objects and at most 1 MiB (1,048,576
bytes). Each inference call consumes the next object. All fields of the selected
`CognitionDecisionV1` or `CognitionDecisionV2` are required except `cycle_id`,
`turn_id`, and `decision_id`. The adapter
always binds cycle and turn IDs to the incoming request; supplied values are
replaced. An omitted decision ID receives a new UUID on each call. Explicit
decision IDs and all operation IDs remain unchanged, so duplicate operation IDs
are still rejected by the runtime. These conveniences are local fixture behavior,
not changes to the public protocol or simulated model intelligence.

Each response identifies its provider and requested model as `script-file`.
Resolved model and token usage remain unknown (`null`). Schema validation occurs
before fresh fixture execution. Each next template is checked against the actual
frozen request before recording invocation-start; an incompatible template blocks
with `model_request_incompatible` without consuming an attempt or the template.
Semantic validation still occurs inside
the runtime; a structurally valid but unsupported operation is recorded and
rejected atomically.

## Run and inspect

```text
cognition run-once --individual-id <UUID> --script sleep.json
cognition check
```

The run prints one JSON object containing `cycle_id`, `status`, and `reason`.
`completed` indicates the bounded cycle terminated; inspect `reason` for sleep,
wait, or a limit. `idle` means no due wake was available. `blocked` means current
lifecycle, governance, model availability or contract compatibility prevented
progress; inspect `reason`. Exit status is 0 for those outcomes,
1 for a failed cycle, or 2 for a command/setup/authorization failure. Error output
does not echo raw SQL, provider exceptions, fixture values, or credentials.

The CLI authenticates before runtime mutation, acquires the dedicated PostgreSQL
ownership session, and releases it on return. Another owner is an error. The
default durable caps are three turns, two attempts per turn, sixteen wakes, and
120 seconds per cycle. The command accepts no flags to increase those limits.

## Recovery and current scope

Rerunning advances an existing active cycle before claiming another wake batch.
An already committed decision is reused and is never regenerated. A started
invocation with no committed result may invoke again with the exact persisted
request. The fixture cursor itself is process-local and restarts at the first
array element on each command invocation; it is not a durable model session.
The CLI refuses fresh inference from a frozen context recorded for another
adapter or requested model, even if the current active configuration was changed.
It can apply an already committed decision from that provider without resampling.
Choose the recovery fixture deliberately when testing a crash before result
commit. Fixture exhaustion blocks with `model_unavailable` before another invocation
is recorded; it does not spend attempts or discard the pending turn.

For a committed decision, `--script` is optional. Recovery applies that exact
decision before requiring a script or checking the current model configuration.
A missing or invalid script cannot prevent a committed sleep decision from being
applied. If the decision continues the cycle, the runtime freezes the next request
and blocks with `model_unavailable`; a supplied script may then continue it if
compatible. Authentication, governance and singleton ownership still apply.

Current focus and explicit future wakes were the initial supported internal
effects. The personal-state increment adds goals, commitments, beliefs and episodes.
Unsupported semantic operations are rejected with no partial application.
Pause or blocked inference prevents new inference and application;
a result received after pause may be retained for later recovery. Sleep does not
invent activity or experience between recorded instants.

Context snapshots preserve the complete canonical request, configuration identity,
retrieval references and reasons, conservative input estimate, and SHA-256 digest.
The compiler drops optional evidence when it cannot fit and fails before inference
if mandatory context exceeds the budget. The estimate is UTF-8 bytes plus a fixed
framing reserve; future live adapters must account for their own actual framing
and schema tokens before dispatch.

Fresh requests use the [attention policy](attention.md), including bounded lexical
recall from focus and wake purposes. Migrate to `0011_reflection_state` before using
the current CLI. Search needs no provider credentials. Its query, selected refs and
reasons are retained in the context snapshot; a resumed frozen request does not
repeat retrieval or substitute current search results.

The runtime also persists an [adaptive heartbeat](autonomy.md). `run-once` does not
remain running to wait for that deadline. It still requires another invocation to
claim a future wake; no daemon or live provider is installed by this increment.
It also schedules [managed reflection](reflection.md) when staged personal state
needs review. Reflection remains subject to the same ownership, pause, configuration
and exact-decision recovery boundaries.
