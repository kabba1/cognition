# Durable inbound perception

Phase 5a adds a bounded ingress boundary: fetch outside PostgreSQL, then atomically
persist observation evidence, deduplication identity, checkpoint progress and
inbound attention. Migration `0013_perception_state` adds source bindings,
observations, ingestion receipts and inbound wake membership. PostgreSQL remains
canonical. The only production adapter in this increment reads a finite local
JSON fixture; it is not an appendable inbox or a live account connector.

## Register and ingest a fixture

Use the existing local administrator identity and an explicitly configured
`COGNITION_DATABASE_URL`. Migrate the intended database separately; commands never
migrate automatically. Registration is disabled by default. The explicit enabled
flag below is audited together with the immutable adapter and logical stream ID.

```text
cognition connector register --individual-id YOUR-UUID --adapter local_json_v1 --source-id example-stream --enabled --reason "Exercise local perception"
cognition ingest-once --individual-id YOUR-UUID --binding-id RETURNED-BINDING-UUID --source observations.json
```

The source file has this exact format. Unknown fields and duplicate JSON keys fail
validation; occurrence timestamps, when supplied, must include a timezone.

```json
{
  "schema_version": 1,
  "stream_id": "example-stream",
  "items": [
    {
      "external_id": "message-1",
      "occurred_at": "2026-09-23T12:00:00Z",
      "payload": {"text": "An observation from the fixture."}
    },
    {
      "delivery_key": "stable-delivery-2",
      "payload": {"text": "A delivery without a provider event ID."}
    }
  ]
}
```

Each item needs an external ID or stable delivery key. If both exist, the external
ID determines identity. Identifiers preserve case, whitespace and Unicode exactly;
the stream ID is a nonsecret logical name, not a path or credential. Source identity
cannot be rebound and there is no cursor-reset operation. Different bindings have
separate deduplication namespaces.

Repeat `ingest-once` to read successive pages. The cursor binds the complete file's
SHA-256 and an item offset. Editing even one byte after ingestion makes the retained
cursor incompatible. A changed fixture needs an explicitly registered new logical
stream. An initially empty file establishes its hash-bound cursor; repeated EOF
polling changes nothing. The CLI prints counts and checkpoint identity, never raw
source data or cursor contents.

```text
cognition connector disable --individual-id YOUR-UUID --binding-id YOUR-BINDING-UUID --reason "Stop receiving this source"
cognition connector enable --individual-id YOUR-UUID --binding-id YOUR-BINDING-UUID --reason "Resume receiving this source"
```

Disabling a source prevents subsequent ingestion. It leaves already recorded
evidence and wakes intact. Administrator authentication precedes adapter construction.
Ingestion acquires the individual's dedicated runtime ownership lock, so it cannot
run concurrently with another owning cognition process.

## Atomicity and bounded work

An ingress snapshot contains owner, governance and binding revisions plus exact
cursor progress. Fetching occurs with no open SQL transaction. Commit locks the
individual, governance and binding, then compares the complete snapshot. Pause and
resume, disable and enable, another committed page, or inbound-wake sealing during
fetch invalidate the old page. Losing the original advisory-lock connection fences
the commit; the runtime never silently reconnects it.

Ingress requires active lifecycle and an enabled source. Inference blocks, external
action blocks and reconciliation requirements do not themselves prevent receiving
evidence. They do not grant permission to infer or act. Provider acknowledgement is
absent; a future acknowledging adapter needs durable pending tokens and retry
semantics, not just a call after database commit.

Policy 1 bounds a page to 32 items and 256 KiB of canonical normalized JSON, each
item to 32 KiB, an ID to 512 UTF-8 bytes, cursor/authentication metadata to 4 KiB,
JSON nesting to 16 levels, and the complete local fixture to 1 MiB. Paging accounts
for wrapper and cursor bytes as well as item count. Nonfinite numbers, malformed
JSON, NULs, invalid Unicode and missing delivery identities reject the whole page.
No automatic skip or quarantine silently drops poison evidence.

Every new observation produces an immutable event envelope and separated content.
Retries compare a versioned fingerprint of normalized occurrence time, content and
adapter authentication. Identical retries add no observation or wake. Changed
content under the same identity rejects the entire page before any member writes.
Redacted content remains redacted on replay. An empty or duplicate page may advance
the cursor; unchanged progress with no new evidence is a true no-op. Every meaningful
commit advances the independent cursor revision exactly once.

An immutable receipt hashes its complete metadata and links to the previous
checkpoint. Binding progress must agree with both retained receipt rows and the
latest ingestion marker envelope, independently of redactable content. Read-only
diagnostics inspect the full chain, ownership and observation counts. A stored page
hash is a commitment; it cannot reconstruct redacted source content.

## Evidence, attention and authority

Each inbound wake has a fixed purpose, no generic coalescing key, and at most eight
exact observation refs. A source can append to one unsealed pending group. Its
original due time stays fixed. Full or claimed groups seal permanently and subsequent
arrivals create another group. Duplicate deliveries never join another wake.

When the earliest ordinary due wake is inbound, cognition claims that wake alone
using its normal turn, invocation and time limits. An ordinary non-inbound wake can
start an ordinary-only bounded batch; this may overtake later inbound entries for
one cycle. A locked earliest wake postpones selection. Exploration remains lower
priority than ordinary due work. Orphaned inbound claims fail integrity validation
before generic recovery can replace their identities.

Observation evidence enters the existing bounded context compiler. Large optional
content may be omitted and reported by packing. Consuming a wake means an attention
opportunity completed; it does not prove that every observation was shown or understood.

The runtime fixes `observation.received`, connector source/binding, individual and
receipt times. Actor, subject, causation and correlation remain null. Source-supplied
administrator claims or runtime instructions remain payload. Local fixture
authentication has no authenticated actor and does not confer administrative access.
Authentication metadata is separate from model-facing evidence content. External
occurrence times remain source claims; development maturation uses recorded time.

## Remaining work

Entity identifier binding and merging require explicit owned provenance and
historical semantics. No sender string currently becomes an actor entity. Network
connectors, durable acknowledgement recovery, scheduling/daemon operation, quarantine,
live models and external action execution are separate increments. Public
Observation/Event and executive contracts remain unchanged.
