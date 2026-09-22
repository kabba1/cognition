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
  findings without repair. Phase 2 subsequently adds claimed-wake/cycle checks.

## Scope boundary

The original ticket packet ended at the Phase 1 gate. The user subsequently
authorized continuing through the academic roadmap and filling routine gaps
within its architecture. Gates remain engineering checkpoints, not automatic
stops requiring another prompt.

Each ticket has a separate Git commit and external review bundle. Engineering
checks and internal code review do not claim empirical proof of the product's
long-term continuity or autonomy thesis.

## Phase 2

- One active cycle per individual claims a bounded batch of due wakes. Limits
  persist with the cycle and cannot reset through a restart. Orphaned claimed
  wakes are superseded and requeued with evidence, preserving pending coalescence.
- Each turn retains a canonical request, SHA-256 digest, configuration revision,
  adapter/model identifiers, selected references and retrieval reasons. Short
  repeatable-read transactions produce coherent snapshots. Inference executes
  outside transactions on a detached request while the advisory session stays owned.
- Invocation start commits before inference. A lost response can be sampled again;
  a committed decision cannot. Decision application and all internal effects commit
  atomically. Pause during inference permits recording but defers application.
- Context uses mandatory control/genesis/governance/wakes/focus, then causal,
  explicitly referenced (up to 64), and recent evidence (up to 32). Byte-based
  estimates are conservative packing bounds, not reported provider usage. Live
  adapters must account for their framing and schema. Administrative provenance,
  sensitive content, and redacted content are excluded from model context.
- Phase 2 supports focus and explicit wake requests. Other proposal families are
  retained then rejected atomically until their personal-state/action handlers exist.
  Existing wake/operation identity collisions are durable rejections.
- Provider exceptions and diagnostic messages are not persisted verbatim. Invalid
  results, including strings PostgreSQL cannot store, consume bounded attempts.
  Structurally valid but semantically invalid decisions remain exact evidence.
- The authenticated `run-once` CLI uses only bounded local JSON fixtures. It binds
  inference to the persisted adapter/model configuration, while allowing recovery
  of an already committed result without invoking its original provider.
- Default cycle limits are 3 turns, 2 attempts per turn, 16 wakes, 120 seconds and
  a 1-second minimum self-wake delay. The deadline gates additional work; it is
  not a forced cancellation of a synchronous adapter already in flight.

## Phase 3: grounded personal state

- Current beliefs, episodes, goals and commitments are explicit relational
  projections. Every mutation retains exact before/after history and a linked
  evidence event. A supersession preserves the old belief and changes its status;
  it never rewrites its proposition. The two revisions share one operation event.
- Every reference is individual-scoped. No sibling operation may introduce another
  operation's target or evidence within the same decision. Duplicate targets,
  forbidden transitions and global operation-ID reuse reject before any effect.
- One individual row lock serializes personal mutation, including internal
  entity/project APIs. Stores refresh stale rows and reject conflicting unflushed
  ORM changes rather than silently replacing them. Diagnostics use column snapshots,
  suppress autoflush and normalize UTC so observation cannot mutate application state.
- Prospective validation can inspect a proposal, but application requires the
  exact stored decision JSON, ID, disposition and digest on a decided turn in an
  active owned cycle. An existing turn ID alone cannot authorize personal effects.
- A belief's accepted/disputed labels require supporting/contradicting references,
  not scalar confidence. These record interpretation, not source truth. Episodes
  require evidence and nonfuture spans. Goals and commitments support deliberate
  volition; fulfillment status does not fabricate a provider receipt.
- Current personal context reads at most eight objects per family. Active goals
  and commitments precede other eligible statuses, then recency and stable identity
  break ties. Each object is packed independently so a large object cannot suppress
  every smaller object in its family. Retrieval does not alter state or strength.
- Runtime contract 3.0 adds four operation families without changing public v1
  protocol schemas. Frozen 2.0 requests retain their original supported effects.
  Interest, preference, self-model and relationship work remains a distinct increment.
