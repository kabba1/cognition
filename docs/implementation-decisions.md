# Implementation decisions

The user authorized continuous implementation of Phase 0 and Phase 1, including
routine completion of unspecified details while preserving the architecture.
The master ticket packet bounds scope. The supplied academic specification's
normative appendices define detailed protocol fields. Earlier detailed ticket
prompts supplement abbreviated ticket descriptions. Research citations in the
supplied prose are context, not implementation dependencies or verified claims.

## Phase 0

- Required nullable fields remain required: callers send explicit nulls rather
  than omit fields in the event, wake, decision, and manifest contracts.
- Schema versions are integer 1; boolean true, float 1.0, and string "1" fail.
- Ref kinds are open-ended, nonempty strings as specified in the new appendices.
- Capability operation metadata owns credentials and network requirements as
  well as effect/retry/verification/cancellation classifications.
- Flexible object fields permit JSON values only. A nonsecret descriptor's
  arbitrary values cannot be proven nonsecret by schema validation. Adapters
  and later exporters must supply only permitted content.
- Model operations perform structural validation only. Whether a referenced
  object exists, a transition is permitted, or authority is sufficient belongs
  to later state/policy validation.
- Model capability views contain definition key/version and allowed operation
  names. They carry no binding credentials or grant-changing mechanism.
- Model usage can be unknown (null). Failure/refusal cannot carry a decision.
- Configuration persists only its typed behavior subset: model, attention,
  retention, sandbox, and config version. Poll cadence and machine paths are
  deployment settings. TOML omission represents optional reasoning effort.
- Fake provider state is separate from Cognition. Same-key native idempotency
  survives a caller timeout. Safe repeat means setting a named resource to an
  identical value; unsafe message retries can produce duplicates.

## Phase 1

- PostgreSQL owns canonical state. Stores join caller transactions and return
  detached snapshots. Supported identity updates cannot rewrite genesis.
- Event sequence is a unique identity counter; rollbacks may leave gaps.
  Redaction removes payload, text, and blob reference while keeping metadata,
  provenance, content hash, and an audit reference.
- Config replacement locks the individual even before its first revision exists.
  Typed behavior is revalidated before any write, including mutated input objects.
- Pending wakes coalesce atomically on individual and coalescing key. The earliest
  due time wins, context references are combined, and additional causal events
  remain context references. Evidence itself is never consumed by coalescing.
  Incoming cause events are checked for existence and individual ownership before
  both insertion and coalescing; a key-share lock protects the validated cause.
- Birth emits `individual.born` and one bootstrap wake. Behavior changes emit
  `config.changed`. Administrative events use `admin.<operation>`.
- Runtime ownership is a session advisory lock on a dedicated physical connection.
  Observation rows confer no authority. A disconnected ownership connection may
  not reconnect. Closing it releases ownership; a successor marks stale rows crashed.
- Only `active` is runnable. Administrative transitions are deliberately explicit:
  `active -> paused` (pause); `paused/quiescent -> active` (resume);
  `active/paused -> quiescing` (begin quiesce); `quiescing -> quiescent`
  (complete quiesce); `quiescing -> paused` (abort quiesce); and
  `paused/quiescent -> retired` (retire). Every other transition is rejected.
  Aborting quiescence returns to paused so autonomy does not resume implicitly.
- Emergency blocking is available in every lifecycle state and always records
  an audit and event. It does not depend on inference. Resuming lifecycle does
  not remove an existing external-action block.
- The CLI authenticates `local_os` from the current Windows token SID or POSIX UID.
  A corresponding active per-individual admin principal must already exist.
  Usernames from environment variables and social event content confer no authority.
- CLI database operations reject any schema other than the supported exact revision.
  Schema migration is an explicit operator command. Integrity checks report
  findings without repair; claimed-wake/cycle checks remain explicitly unavailable.

## Scope boundary

This implementation run ends at the Phase 1 acceptance gate. Phase 2 cognition
execution, personal-state persistence, provider adapters, consequential action
execution, and deployment are not inferred from the research roadmap.

Each ticket has a separate Git commit and external review bundle. Engineering
checks and internal code review do not claim empirical proof of the product's
long-term continuity or autonomy thesis.
