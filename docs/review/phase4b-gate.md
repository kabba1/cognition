# Lexical retrieval engineering gate

**1,757 tests pass** in one clean full PostgreSQL 18 run on Python 3.13/Windows
(726.75 seconds). Ruff check and format check pass for 195 Python files; strict
mypy passes for 85 source files. [Full test output](phase4b-tests.txt).

This increment adds 54 cases: 12 schema/index cases, 23 PostgreSQL retrieval cases,
12 pure compiler cases and seven end-to-end acceptance cases. Earlier tests pass.

Migration 0009 adds three bounded GIN expression indexes over beliefs, episodes and
event text. Canonical text is preserved, including large Unicode values. Queries
use explicit PostgreSQL English normalization, bound plain-text expressions and
bounded focus/wake allocation. Owned current beliefs and eligible event text are
filtered before ranking and per-corpus limits. Search does not read payload JSON,
private provenance or administrative content.

Fresh coherent context receives up to eight matches per corpus. Rank survives
packing within each corpus; corpora take turns below urgent/direct/linked recall
and above recent-only content. Stronger retrieval reasons win duplicates. Query
terms, first origins, effective expression and policy limits remain inspectable
data with no authority. Query and summary bytes are reserved before optional
packing. Already frozen requests retain their exact bytes after search-state or
configuration changes.

Independent review found a focus-allocation edge case: a stop-word prefix could
lose the focus's useful term to 16 wakes. A second source round-robin after bounded
normalization fixes it, with a regression. Review also added consistent rejection
of foreign detached lexical events and explicit query-source cap metadata. Final
independent review found no remaining concrete issue in retrieval, compiler,
runtime integration, migration or the schema comparison helper.

PostgreSQL deparses equivalent configured index expressions differently from their
metadata spelling. Test-only comparison proves equivalence with PostgreSQL-parsed
temporary sibling indexes in rolled-back savepoints. Negative controls preserve
real dictionary, prefix, access-method and unexpected-index drift; no product or
global Alembic comparison suppression was added. Query-plan tests establish index
eligibility, not a production latency bound.

Acceptance demonstrates recall of old beliefs, episodes and event text beyond
recent pools without their IDs and with a fresh executive; protected-event and
foreign exclusions; urgent/direct precedence; unchanged frozen recovery; and hostile
matched text retaining no administrative or external-action authority.

English matching, prefix limits and term caps can miss relevant memory. Limits
bound returned candidates, not all database work. This gate does not establish
empirical lifelong recall quality. Adaptive heartbeat, reflection/exploration,
live providers and an unattended service remain subsequent work.
