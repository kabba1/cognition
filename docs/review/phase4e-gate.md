# Phase 4e gate: explicit bounded internal exploration

The frozen implementation passed the complete PostgreSQL suite on September 23,
2026: **2,198 passed in 1162.83 seconds**, with no failures or skips. Source was
frozen at `b7b6a66`; only documentation changed during the gate. See the retained
[test output](phase4e-tests.txt). Ruff passed, formatting checked 245 files, and
strict mypy passed all 100 source files.

## Delivered behavior

Migration `0012_exploration_state` retains operational cadence and immutable
internal grants, with populated downgrade guards. Exploration is disabled unless
explicitly enabled through its strict governance subtree. The authenticated local
operator toggle records exact policy/revision changes without rewriting active work.

A grant permits one turn, two durably recorded starts, one wake and a 120-second
start deadline, reduced by tighter caller limits. Exploration runs separately and
below ordinary due work. Completion or cancellation preserves a minimum seven-day
cadence, including toggles, failure, long silence and process interruption. The
executive may choose sleep/no-op or grounded personal effects; wake requests reject
the whole exploration decision. External action authority remains blocked.

Exact decided D1 applies before current exploration permission or model availability
is needed. Spent, expired or disabled retry-needed work settles before context or
adapter preflight. Permission linearizes at durable invocation-start commit, so a
later disable preserves the authorized result while preventing subsequent starts.
Mandatory frozen control binds exact grant/cycle limits and reserves context space.

Retained creation/outcome envelopes identify historical grants after content
redaction. State pointers must agree with latest retained outcome/completion
history; cancelled work never invents a cycle or clears the most recent real one.
Diagnostics inspect policy, scope, membership, physical starts and all retained
history without flushing or refreshing caller ORM state.

## Review and regression evidence

Independent review found and resolved these cases before the final freeze:

- `SKIP LOCKED` could make a temporarily locked ordinary wake look absent. The
  exploration fallback now checks ordinary due visibility before taking priority.
- Mutable live timing could precede the retained state floor; both pending and
  claimed grants now validate that floor. Null outcome timestamps fail cleanly.
- A cancellation could retain an ordinary or mismatched terminal cycle; retained
  terminal state now requires its actual exploration grant and completion envelope.
- Foreign state pointers with missing grant metadata could evade discovery;
  classification now considers any retained pointer before ownership/kind filters.
- Cleared or stale-valid outcome pointers could resemble initialization. Exact
  latest-envelope ordering now guards both cadence and the last real terminal cycle.

The final suite includes 204 new cases over Phase 4d, spanning schema, strict policy,
administration, scheduling/scope, context budget and tampering, v1/v2 execution,
disable/recovery, concurrency, diagnostic corruption and retained-anchor integrity.
Focused old runtime/admin/birth/integrity regressions passed before the full run.

## Limits and continuation

The cadence and caps are experimental engineering choices, not measures of
curiosity or a cumulative spending budget. Legitimate personal changes may affect
later ordinary attention. The deadline limits starts, not a hard provider timeout.
This remains a local bounded runner without a live provider or unattended service.

Next work is bounded inbound perception: source identity, deduplication, atomic
event/observation/cursor/wake persistence and a deterministic local source adapter.
Entity identifier binding, external execution, portability, operator UI and live
trials remain separate implementation boundaries.
