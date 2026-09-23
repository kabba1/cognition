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

All seven existing personal operation families participate in one atomic decision
plan. Incompatible proposals reject as a whole. Public protocol v1 remains stable;
runtime contract 3.1 adds handlers while frozen 3.0/2.0 requests retain their earlier
operation support. External action requests remain blocked.

## Engineering evidence

- [Phase 2 gate](review/phase2-gate.md): 898 passing tests.
- [Phase 3a gate](review/phase3a-gate.md): 1,146 passing tests.
- [Phase 3b gate](review/phase3b-gate.md): all 1,289 cases verified across the full
  run and a 17-case database-startup recheck; Ruff and strict mypy pass.

See [personal state](personal-state.md), [Phase 2 operations](phase2-operations.md)
and [implementation decisions](implementation-decisions.md) for exact semantics.
The [Phase 0–1 handoff](architect-handoff.md) remains the original baseline report.

## Next work and remaining boundaries

Next is the relationship/open-thread substrate and social context, followed by
an explicit versioned executive extension for model-authored entities, projects
and relationships. Decision v1 cannot create those objects. Entity identifiers
and goal dependencies also need their explicit semantics.

Later roadmap work includes attention selection, lexical retrieval, heartbeat and
exploration scheduling, perception ingestion, capability execution and external
effect verification/reconciliation, managed workspace, portable restore/migrate/
fork, operator UI and limited live trials. No live provider adapter or unattended
service is available yet. Deterministic recovery tests do not establish empirical
long-term identity, autonomy, retrieval quality or model-swap quality.

The Python runtime can apply an already committed decision without invoking its
original model. The local CLI still requires a valid script and active script-file
configuration before entering recovery; recovery-first CLI ordering is scheduled
with the explicit executive extension and must not be inferred from runtime tests.
