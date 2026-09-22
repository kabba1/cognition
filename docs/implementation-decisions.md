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

## Scope boundary

This implementation run ends at the Phase 1 acceptance gate. Phase 2 cognition
execution, personal-state persistence, provider adapters, consequential action
execution, and deployment are not inferred from the research roadmap.

Each ticket has a separate Git commit and external review bundle. Engineering
checks and internal code review do not claim empirical proof of the product's
long-term continuity or autonomy thesis.
