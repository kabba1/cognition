# Phase 5a design draft: durable inbound perception

This is the next increment after the Phase 4e full gate. Independent review identified
the identity, coalescing, cursor and permission details resolved below. The academic specification calls
for connector-bound deduplication and one atomic observation/event/cursor/wake
transaction, with fetching outside SQL transactions. Existing `ObservationV1` and
`EventEnvelopeV1` remain public contracts; source authentication is evidence, never
administrative authority.

## Scope and alternatives

Start with the inbound persistence and polling boundary plus a deterministic local
source. A network connector would mix provider authentication, deployment and
side effects into the first test of deduplication/recovery. A bare event append
would omit the cursor and replay problem. The selected increment makes these
mechanics executable without a live account or new dependency.

Entity identifier binding/merging remains the next perception increment. A raw
sender string cannot identify an administrator or silently create/merge people.
Actor entity linkage is absent until a trusted owned binding exists. The existing
model-authored entity directory remains available; this design adds no model
operation or public protocol version.

## Durable source identity

Introduce an individual-owned connector binding with an immutable adapter/source
identity, enabled status, opaque cursor, cursor revision and operational revision.
The source identity is a nonsecret logical stream ID, not a file path or sender name.
Uniqueness is scoped to individual, adapter and stream ID; those fields cannot be
rebound to another stream. Credentials and deployment details do not enter this row or model context.
Binding registration/enablement is a narrow authenticated local operation with
evidence and audit. A source cannot select another individual's binding.

An observation retains its event ID, owner, binding, external event ID if present,
stable dedup key, normalized content fingerprint and source authentication metadata.
Uniqueness is scoped to binding plus dedup key. Retain normalized event metadata
separately from redactable content using the existing evidence ledger. A replay
after payload redaction must not recreate redacted content.

The adapter must provide a stable external ID or explicit stable delivery key. If
an external ID exists it determines identity, regardless of a supplied delivery key;
otherwise use the delivery key. Encode the kind and exact UTF-8 value canonically
before hashing, preserving case and Unicode instead of guessing provider semantics. Do
not silently deduplicate two unrelated ID-less observations merely because they
have equal text. Missing stable identity is a batch validation failure. A duplicate
with the same immutable source content is a no-op; a reused identity with changed
content is a conflict, not permission to rewrite the first observation. Fingerprint
comparison excludes newly assigned runtime event IDs, receipt timestamps and the
fallback key when an external ID is present, so legitimate retries compare equal.
Persist identity/fingerprint policy version 1. Fingerprint the normalized occurrence
time, content type, text/payload and adapter-produced authentication metadata.
Changed authentication cannot upgrade a retained observation: it is a conflicting
retry under the same identity. Same-page duplicates follow the same exact comparison.

## Bounded two-stage polling

An owning runtime reads a detached binding/cursor snapshot in a short transaction,
commits it, and fetches one bounded page outside SQL. A second short transaction
locks individual, governance and binding in that order, rechecks lifecycle/source
permission and compares cursor, binding, individual and governance revisions with
the detached snapshot. A disable/re-enable or pause/resume during fetch therefore
invalidates the page even when final flags look unchanged. Verify that the original
dedicated advisory-lock connection survives fetch before commit. Stale work must not
overwrite progress or acquire a replacement ownership connection implicitly.

Validate the entire page and normalized observations before writing any member.
Policy 1 limits one page to 32 items and 256 KiB canonical UTF-8 bytes, an item to
32 KiB, opaque IDs to 512 UTF-8 bytes, cursor/authentication metadata to 4 KiB and
JSON nesting to 16 levels. Reject nonfinite numbers, NUL/non-UTF-8 strings and
oversized values before persistence. Reject malformed
members atomically without source content in error messages. This conservative
first policy can leave a poison page pending; skipping/quarantine would need a
separate audited operation rather than silently dropping evidence.

Commit new events, observation identities, connector-owned inbound wakes,
cursor advancement and ingestion evidence together. Empty pages may advance a
cursor. Duplicate-only pages advance legitimate progress without another wake.
An unchanged cursor with no new observations is a true no-op, including no new
polling event or revision. Arrival order is ledger order; external occurrence times
remain claims and may be out of order.

Source acknowledgement is deliberately outside Phase 5a. A bare after-commit call
cannot recover a crash before acknowledgement; a future acknowledging adapter
needs durable pending tokens and idempotent retry semantics. The existing fake's
acknowledgement method remains compatible but this runtime does not call it.
No external message sending or provider mutation is part of this increment.

## Connector-owned bounded attention

The generic coalescer is unsuitable: its keys are model-selectable and its refs and
purposes grow across pages. Persist an inbound-wake record and permanent
observation-to-wake membership, with at most eight observations per wake. Use null
generic coalesce keys, a fixed short runtime purpose and exactly the owned member
event refs. A binding may retain one unfilled pending pointer. Append only to that
validated pending wake; preserve its original due time. Full or claimed wakes are
sealed and new arrivals rotate to a new wake due at runtime now. Retain every
observation's grouping; duplicate deliveries do not join again.

Bound aggregate mandatory context as well as each page. For a new ordinary cycle,
inspect the earliest due ordinary wake by `(due_at, wake_id)`, classifying durable
inbound membership before mutable kind/owner fields. If it is inbound, claim that
one wake alone. Otherwise claim a normal bounded ordinary batch excluding inbound
wakes. That batch may overtake later inbound entries within one cycle; no per-source
round-robin guarantee is claimed. A locked earlier wake conservatively postpones
the fallback. Exploration remains below all ordinary due work. Existing active
cycles keep membership and limits unchanged.

Classify and validate managed inbound claims before the generic orphan-wake
recovery helper. A claimed inbound wake lacking its owned active cycle fails closed;
superseding it with a new generic wake would detach permanent observation membership.

An inbound cycle keeps normal turn/attempt/time limits; only its wake membership
is isolated. Validate exact membership and the eight-ref limit on claim, load and
recovery. Test maximum mandatory projection with default configuration. Large
optional evidence still uses existing whole-object packing and omission accounting.
Consuming a wake records an attention opportunity, never proof that every referenced
observation was rendered or semantically processed.

## Authority and context

The runtime fixes owner, source kind/binding, observation event type and receipt
times; source payload cannot supply administrative envelopes, governance changes,
runtime control sections or inferred personal state. Runtime construction fixes
`event_type=observation.received`, connector source/binding, null actor/subject/
causation/correlation and runtime receipt timestamps. Source authentication
assertions remain untrusted provenance until a separate binding policy interprets
them. Neither authentication metadata nor source credentials are copied into
model-facing content.

Inbound text/payload enters the existing evidence category and wake references.
It may be retrieved, interpreted and cited through the normal bounded context
compiler. Prompt injection text remains evidence even after episodic/belief
interpretation. Birth, runtime, model and admin event kinds remain unavailable to
the connector normalization path.

Ingress requires an active individual, existing governance and an enabled binding.
It performs no inference or external effect: `inference_blocked`,
`external_actions_blocked` and `reconciliation_required` do not themselves prohibit
recording incoming evidence. Global lifecycle pause does. Snapshot revision checks
still reject policy/lifecycle changes during a fetch, allowing an explicit retry
under the new state. This permission is separate from cognition's `execution_allowed`.
The registered binding is the explicit source allowance, not a social request.

## Concrete first adapter and interfaces

Move the existing connector item/malformed-item/batch data records into
`connectors/base.py` and re-export them from `testing.fake_connector`; preserve old
constructors and behavior. Scripted poll steps remain testing helpers. Add only a
trailing optional `delivery_key` item field.
The existing fake can be wrapped with adapter/source metadata rather than requiring
old tests to supply new constructor arguments. No acknowledgement requirement enters
the production polling protocol.

Use a detached binding snapshot containing binding/owner, adapter/stream IDs,
cursor, cursor revision, binding revision and individual/governance revisions.
`normalize_page(snapshot, batch, observed_at)` returns a bounded detached normalized
page. `persist_page(session, snapshot, page, recorded_at)` revalidates it and joins
the caller transaction, returning created event IDs, duplicate count, wake IDs and
new cursor revision. `ingest_once(owner, binding_id, connector, clock)` owns the
short read/fetch/short write sequence. Polling adapter/source IDs must match the
binding before polling, and the local adapter verifies its file's stream ID.

The concrete `local_json_v1` adapter reads an explicit source file of at most 1 MiB
before parsing. Its exact document is a version-1 object with `stream_id` and an
ordered `items` list. Each item has external ID or delivery key, optional aware
occurrence time, and JSON-object payload. Reject unknown item/document fields,
duplicate JSON keys, invalid types, excessive nesting and nonfinite numbers.
The restart-stable cursor contains the complete fixture SHA-256 and item offset;
edited bytes conflict with a retained cursor. This adapter represents a finite
fixture stream, not a live appendable inbox. An edited/new stream needs an explicit
new logical source/binding. Decode cursors strictly: exact file hash and integer
offset excluding booleans, in the range zero through item count. Pack the largest
ordered prefix fitting both count and canonical page-byte bounds, including wrapper
and cursor overhead; a single oversized normalized item is invalid. Do not let a
collection of individually valid items create a permanently oversized page. An
initially empty fixture still commits its hash-bound offset-zero cursor. Subsequent
EOF returns that unchanged cursor and an empty page.

Payload remains source evidence. A string `payload.text` may also be projected into
event text for existing lexical recall; other payload keys confer no authority.
The runtime uses `application/json`, internal sensitivity, history retention, no
blob pointer and fixed local-fixture authentication metadata without an authenticated
actor. The source cannot select retention, sensitivity or authenticate itself as a
local administrator. Do not label canonical JSON fingerprints as exact raw-byte
hashes; `raw_content_hash` remains null unless the adapter actually supplies that
separate evidence in a future version.

Add authenticated `connector register/enable/disable` and `ingest-once` CLI paths.
Require owner/binding identity and a reason for administrative changes. Registration
starts disabled unless explicitly enabled in the audited request. Administrative
lock order is individual, administrator, governance, binding. Polling authenticates
the real local OS principal and acquires runtime ownership. No flags override
canonical event owner, principal, cursor or authority. Error output never includes
source payload, credentials, cursor contents or raw provider exceptions.

## Verification and open review points

Use real PostgreSQL and deterministic connectors to verify duplicate and changed
replay, separate-binding keys, missing IDs, out-of-order timestamps, malformed and
oversized pages, rollback of every stage, cursor races, pause/disable during fetch,
crash before/after commit, absence of source acknowledgement, redaction replay,
claimed/pending wake isolation, fresh-model evidence context, injection resistance
and dirty-session read-only diagnostics. Migration downgrade must refuse retained
binding or observation history.

Preserve existing FakeConnector behavior with compatibility tests. Verify maximum
mandatory context, source-key impersonation, same-page identity conflicts and stale
binding/lifecycle epochs in addition to cursor races. Do not claim a complete perception system or start a
network connector merely because the ingestion foundation exists.
