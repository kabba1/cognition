# Phase 5a gate: durable inbound perception

The complete PostgreSQL suite passed on September 23, 2026: **2,444 passed in
1505.56 seconds**, with no failures or skips. Source was frozen at `077528c`;
only documentation and subsequent design work changed during the run. See retained
[test output](phase5a-tests.txt). Ruff passed, formatting checked 272 files and strict
mypy passed all 112 source files.

## Delivered behavior

Migration `0013_perception_state` adds immutable source identity, permission/cursor
epochs, observation metadata, bounded inbound groups and ingestion receipt chains.
Authenticated local operators can register, enable and disable sources. The finite
local JSON adapter reads bounded pages with an exact-file-hash cursor. It performs
no acknowledgement, network access or provider mutation.

Fetching and normalization occur outside SQL transactions. Commit rechecks lifecycle,
governance, source and cursor epochs under canonical locks on the original advisory
ownership connection. A whole page's evidence, receipt, progress and attention commit
together. Changed duplicate identities reject before writes. Identical retries never
restore redacted content; unchanged progress without new observations is a true no-op.

Inbound groups retain at most eight immutable observation refs, a fixed purpose and
due time, and no generic coalescing key. Full or claimed groups seal. An earliest
inbound wake runs alone with normal cognition limits; ordinary batches exclude
inbound groups and exploration stays below ordinary due work. Managed orphan claims
fail before generic recovery can detach membership.

Runtime-fixed observation envelopes keep source claims separate from administration.
Source authentication is metadata, not authority. Actor attribution remains absent.
Read-only diagnostics inspect retained observation, receipt, marker, cursor and wake
history, including after content redaction. Public contracts remain unchanged.

## Review and verification

Independent implementation review and regression tests resolved these issues:

- A reclassified newest ingestion marker combined with rewound state could hide a
  retained newer receipt. Independent latest receipt and marker identities now agree.
- A missing pending pointer could hide an unsealed inbound group. Independent open
  membership discovery now precedes both writes and duplicate-only no-op returns.
- Ingestion and claim helpers could flush unrelated pending ORM authority/content
  changes. Mutations now reject all unflushed caller state.
- Receipt observation time could precede source registration, and a fully sealed
  group could be claimed before its seal time. Both clocks now fail validation.
- Missing unredacted payload could be mistaken for intentional redaction during
  diagnostics. Retained content now undergoes fingerprint verification.

The suite adds 246 cases over Phase 4e. It includes 13 acceptance/recovery cases for
SQL-stage rollback, fresh-adapter replay, later-page recovery, cursor/governance races,
backdated occurrence claims, malformed-page atomicity, and eight-ref model context
with hostile source claims retaining evidence-only status. Separate tests cover
ownership loss, permission changes during fetch, migrations, administration, byte
bounds, corruption and dirty-session diagnostics. Existing cognition, attention,
exploration, administration and integrity regressions passed before the full run.

An initial full-suite collection attempt found duplicate unit/integration test module
names; renaming the unit normalization module fixed collection before the frozen run.

## Limits and continuation

The adapter is a finite fixture reader, not a live inbox. Source acknowledgement needs
durable pending-token recovery before a provider adapter can use it. Large optional
evidence can be omitted by bounded context packing; wake consumption records an
attention opportunity, not proof that every observation was shown or understood.

The next increment is audited source-scoped entity identifier bindings with explicit
revocation/reassignment history. It will not reinterpret payload names as authenticated
actors or destructively merge entities. Semantic merge, live connectors/models,
external execution, portability, operator UI and live trials remain separate work.
