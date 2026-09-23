# Relationship substrate engineering gate

**1,372 tests pass** in one clean full PostgreSQL 18 run on Python 3.13/Windows
(512.27 seconds). Ruff check and format check pass; strict mypy passes for 78
source files. [Full test output](phase3c-tests.txt).

This increment adds 27 schema cases, 37 relationship-store cases, six runtime
acceptance cases, four directory-context cases and nine social-integrity cases.
All earlier phase tests also pass, including the Phase 3b cases whose previous
aggregate gate needed a database-startup recheck.

Relationships retain an immutable owned entity, subjective narrative and evidence.
Open threads retain their relationship, evidence and optional commitment link.
Trusted stores use one individual lock, exact before/after histories and caller
transactions. Terminal threads cannot reopen; linking a commitment never changes
its state. Birth creates no social records.

Context renders bounded relationships, threads, projects and entity descriptions.
Top-level references identify rendered records only, not nested pointers. Fresh
adapters recall these records without a transcript. Social text cannot confer
administrative identity or unblock external actions. Diagnostics check owned links,
evidence, current projections, revision chains and immutable parent history.

Independent review is clear. Required versus optional text validation was corrected
during implementation, then regression/static tested. Public v1 schemas remain
unchanged: cognition can reference existing social records but needs the next
explicit protocol extension to create or revise them.
