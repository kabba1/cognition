# Staged personal development engineering gate

All **1,289 cases are verified** on PostgreSQL 18, Python 3.13 and Windows.
The full run produced 1,272 passes and 17 setup errors while the local database
was still recovering after restart. All 17 errored cases passed on rerun once
the cluster accepted connections. No product source changed between runs.
This is aggregate coverage across those two runs, not a claim of a single clean
full-suite execution. [Test evidence](phase3b-tests.txt).

Ruff check and format check pass; strict mypy passes for 76 source files.
New coverage includes 31 schema cases, 80 development-store cases and 18
development acceptance cases, plus frozen-contract and integrity regressions.

Implemented existing v1 interests, preferences and layered self-model operations
with candidate/pending states, durable eligibility times, explicit reflection,
distinct grounding anchors, hysteresis and exact revision history. All seven
personal operation families share atomic validation/application and exact D1
recovery. Runtime contract 3.1 preserves frozen 3.0/2.0 operation boundaries.

Independent review identified and verified corrections for:

- Claim-specific evidence: a replacement self-description uses its own support;
  previous claim/support remain in history rather than transferring silently.
- Executable reflection guidance: context includes the exact self-scheduled wake
  target, anchor eligibility/spacing/novelty, exclusions and layer-specific rules.

Experimental 24-hour/seven-day thresholds are engineering choices. These tests
demonstrate deterministic state behavior, not empirical personality or autonomy.
Relationships and the explicit executive protocol extension remain next work.
