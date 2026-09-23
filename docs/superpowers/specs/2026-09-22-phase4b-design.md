# Phase 4b: PostgreSQL lexical memory retrieval

After the Phase 4a gate, add lexical candidates to coherent attention. The academic
specification explicitly chooses PostgreSQL FTS before embeddings. This increment
searches beliefs, episode summaries and event text; it does not add providers,
connectors, embedding storage, a second canonical memory store or background jobs.

## Database implementation

Use three GIN expression indexes in migration 0009, with matching SQLAlchemy
metadata: belief proposition plus topic, episode summary, and event-content text.
Use an explicit `pg_catalog.english` text-search configuration in both index and
query expressions. PostgreSQL documents matching configured expressions for index
use and supplies native query/ranking functions. See its official
[index guidance](https://www.postgresql.org/docs/18/textsearch-tables.html) and
[query controls](https://www.postgresql.org/docs/18/textsearch-controls.html).

Searchable projections are bounded prefixes: 8,192 characters for a primary text
field, plus 1,024 characters for belief topic. Null fields become empty strings.
Do not index unbounded canonical text: a large canonical record must not cause a
future insert/revision to fail because a derived tsvector exceeds PostgreSQL limits.
Use immutable expressions, not application-maintained copies. PostgreSQL updates
the index with each source-row change. Downgrade removes indexes only.

Current belief eligibility is tentative, accepted or disputed. Explicit reference
recall still permits superseded/withdrawn beliefs, but automatic lexical recall
does not treat them as current. Episodes remain append-only stored summaries.
Event matches require the requested individual, existing content, nonnull text,
public/internal sensitivity, no redaction, non-admin source, and event type other
than individual.born (whose raw text is deliberately excluded by its renderer).
Apply eligibility and ownership before ranking and per-corpus limits.

The event index may include rows excluded by the joined event-metadata filter;
those rows must never enter result counts, ranking comparisons or model context.
Search excludes payload JSON, blob pointers, source IDs, actor metadata and
administrative records. Reuse sanitized event rendering. Existing retain_until
metadata is not a visibility gate before actual redaction; do not invent a new
expiry policy only for search. Derived belief/episode erasure after source redaction
is a separate semantic requirement, not supplied by this increment.

## Query policy 1

Derive queries deterministically from owned wake purposes and current-focus
summary, without a model call. Use at most the first 16 wakes sorted by due time
and stable ID, plus focus. Put focus first in source allocation so 16 wakes cannot
exclude it solely by source position; then use the ordered wakes. Clip each source
to 512 characters, extract at most 32 alphanumeric Unicode words per source, and
skip words longer than 64 characters. Also skip a word cut by the source prefix
boundary. Allocate raw terms by round-robin over sources, avoiding one verbose
wake consuming the entire budget. Keep at most 64 distinct raw terms. The 16-term
normalized cap can still omit a later source; it is not unlimited source coverage.

Normalize these bounded terms with PostgreSQL English parsing. Discard empty
stop-word queries and deduplicate equivalent normalized lexeme sets, then retain
at most 16 normalized terms in deterministic source-allocation order. Build the
search query as an OR of bound `plainto_tsquery` expressions for the retained raw
representatives. Treat all input as data; do not interpolate it into SQL or accept
raw tsquery operators. A stop-word-only or empty input returns no lexical candidates.

English normalization is an experimental lexical baseline, not multilingual or
semantic recall. Prefix limits and token limits can miss relevant material.
Record policy version, dictionary, query-term count and caps in attention summary.
Also retain the bounded effective tsquery, raw representatives and first-origin
source for each retained term in a `lexical_query` present-data section with
`authority: none`. Its bytes are reserved before optional results are packed.
Query origins refer to the already frozen wake IDs or current focus; this is query
provenance, not a claim of additional object retrieval. Retain only first origins,
not an unbounded list of every source occurrence. Do not promote query words into
runtime instructions. The section is present only for a nonempty effective query.

## Retrieval and packing

Return up to eight eligible matches per corpus, at most 24 lexical candidates.
Summary candidate counts concern this bounded retrieved pool, not total database
matches. Do not infer a complete match count from a LIMIT query.
Rank within each corpus with ts_rank_cd, followed by descending update/create/event
sequence and stable ID. Rank describes lexical match only; it does not establish
truth, confidence, personality strength, motivation or authority.

Carry detached match rank with candidates so the compiler does not replace it
with recency. Render actual owned records using existing personal/event projections.
No evidence-link hydration, relationship expansion or new references arise merely
from a text match. Retrieval runs with no autoflush and column snapshots, in the
same coherent transaction as the new context snapshot.

Urgent, causal, direct and linked selection outrank lexical recall. Give the three
lexical corpora round-robin packing opportunities, keeping lexical rank within each
corpus, before ordinary recent pools. Deduplicate against higher-priority records;
emit `lexical_match` only when lexical retrieval is why a record was selected.
All selected refs must correspond to rendered content. Whole-object byte packing,
summary reservation and immutable frozen-request recovery remain unchanged.

The per-corpus LIMIT bounds returned/materialized candidates, not all database work
over matching index entries. Verify matching index expressions and inspect query
plans on representative seeded data. Do not claim a benchmark proves a global
query-time bound or empirically validates lifelong memory quality.

## Verification

Real PostgreSQL tests cover index creation/removal, metadata parity and index-use
eligibility; prefix/null/oversized source behavior; normalized words and stopwords;
fair query-source allocation and all caps; deterministic ties; owned current beliefs,
episodes and eligible events; foreign/sensitive/redacted/admin/birth exclusion;
index updates on revision/redaction; no dirty ORM leakage or retrieval mutations.
Include 16 unique wake terms plus unique focus to verify focus allocation.

Acceptance demonstrates an old memory outside recent pools recalled from a wake's
words without its ID, with a fresh executive and no transcript. Cover urgent/direct
precedence, omission accounting, hostile source text retaining no authority, and
exact frozen recovery after searchable state changes. Run independent review and
the complete PostgreSQL/static gate before publishing the checkpoint.
