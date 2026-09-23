# Current implementation status

The repository is the executable baseline. The academic specification remains
the architectural direction; a concept in that document is not automatically an
implemented subsystem. Earlier gate reports describe their historical checkpoints.

## Implemented

- Phase 0–1: versioned protocol contracts; PostgreSQL migrations; immutable
  identity and governance; separated evidence metadata/content; durable config and
  wakes; atomic birth; dedicated advisory-lock runtime ownership; authenticated
  administrative lifecycle controls; integrity diagnostics and deterministic fakes.
- Phase 2: bounded cognition cycles and turns; coherent frozen context;
  interchangeable synchronous model boundary; persisted invocation start/results;
  exact decision recovery; atomic validation, focus and wake effects; authenticated
  local script-file runner. Provider calls occur outside database transactions.
- Phase 3a: evidence-linked goals, commitments, beliefs and episodes; exact
  before/after histories; owned entity/project substrate APIs; individual-scoped
  references; bounded current-state context; diagnostic and recovery coverage.
- Phase 3b: tentative interests/preferences; layered presentation, narrative,
  self-belief and value state; durable pending proposals and eligibility times;
  reflection and distinct evidence gates; exact claim-specific support histories;
  bounded context with executable development guidance. Policy thresholds are
  experimental choices, not validated measures of personality.
- Phase 3c: relationship narratives and open threads through trusted internal
  stores; immutable social parent links; evidence and exact revision history;
  bounded relationship/thread/entity/project context and integrity diagnostics.
  Decision v1 can reference these objects but cannot create or revise them.
- Phase 3d: explicit
  configuration schema 2 and cognition protocol 2; model-authored entity/project/
  relationship/thread operations; strict version dispatch and frozen compatibility;
  configuration/request/result linkage checks; recovery before fresh-inference
  adapter or CLI script requirements. Existing v1 public contracts remain stable.
- Phase 4a: bounded direct and
  linked personal recall; mandatory urgent commitment details and overflow notice;
  deterministic packing with family diversity; selection reasons and omission
  accounting. Existing frozen snapshots remain unchanged.
- Phase 4b: bounded PostgreSQL English retrieval over owned current beliefs,
  episodes and eligible event text; ranked corpus diversity, query provenance and
  stronger-reference precedence. Search indexes preserve canonical text.
- Phase 4c: durable managed heartbeat, operational backoff across restarts,
  commitment deadline shortening, exact terminal accounting, deferred pause
  materialization and read-only scheduler integrity checks.
- Phase 4d: bounded managed reflection for staged interests/preferences and pending
  inferred self-state; family rotation, durable immutable target scope, delayed
  cadence, exact terminal accounting and historical integrity diagnostics.

All eleven v2 personal operation families participate in one atomic decision plan.
Incompatible proposals reject as a whole. Protocol v1 retains its seven families
under runtime contract 3.1; frozen 3.0/2.0 requests retain their earlier support.
Protocol v2 selects contract 3.2 through explicit configuration history. External
action requests remain blocked. See [executive protocols](executive-protocols.md)
for selection, exact frozen combinations and recovery semantics.

## Engineering evidence

- [Phase 2 gate](review/phase2-gate.md): 898 passing tests.
- [Phase 3a gate](review/phase3a-gate.md): 1,146 passing tests.
- [Phase 3b gate](review/phase3b-gate.md): all 1,289 cases verified across the full
  run and a 17-case database-startup recheck; Ruff and strict mypy pass.
- [Phase 3c gate](review/phase3c-gate.md): 1,372 passing tests in one clean full
  PostgreSQL run; Ruff/format and strict mypy pass.
- [Phase 3d gate](review/phase3d-gate.md): 1,665 passing tests in one clean full
  PostgreSQL run; Ruff/format and strict mypy pass.
- [Phase 4a gate](review/phase4a-gate.md): 1,703 passing tests in one clean full
  PostgreSQL run; Ruff/format and strict mypy pass.
- [Phase 4b gate](review/phase4b-gate.md): 1,757 passing tests in one clean full
  PostgreSQL run; Ruff/format and strict mypy pass.
- [Phase 4c gate](review/phase4c-gate.md): 1,859 passing tests in one clean full
  PostgreSQL run; Ruff/format and strict mypy pass.
- [Phase 4d gate](review/phase4d-gate.md): 1,994 passing tests in one clean full
  PostgreSQL run; Ruff/format and strict mypy pass.

See [personal state](personal-state.md), [Phase 2 operations](phase2-operations.md)
and [implementation decisions](implementation-decisions.md) for exact semantics.
The [Phase 0–1 handoff](architect-handoff.md) remains the original baseline report.

## Next work and remaining boundaries

Next is explicit bounded internal exploration. Entity identifier
binding/merging and goal dependencies still require explicit semantics and are
not implied by the new executive operations. Decision v1 still cannot create
entities, projects or relationships; those proposals require selected protocol 2.

Later roadmap work includes exploration scheduling, perception ingestion,
capability execution and external
effect verification/reconciliation, managed workspace, portable restore/migrate/
fork, operator UI and limited live trials. No live provider adapter or unattended
service is available yet. Deterministic recovery tests do not establish empirical
long-term identity, autonomy, retrieval quality or model-swap quality.

The Python runtime and local CLI can apply a compatible committed decision without
its original model or a script. They retain authentication, lifecycle, governance,
schema and runtime-ownership guards. If recovery continues to a turn needing
fresh inference, that turn requires a compatible adapter; absence blocks before
invocation-start. This recovery path does not supply a live model or an unattended
service.
