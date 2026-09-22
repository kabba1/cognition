# Architect handoff: Cognition Phase 0–1

## Current position

All Phase 0 and Phase 1 tickets from the supplied master implementation packet
have been implemented. The implementation ends at commit
`0af937bdb4913802d005e1dccf8d9565abcb1e87`; subsequent handoff commits add review
documentation and verification evidence only.

**Engineering gate: PHASE 0 EXIT: PASS; PHASE 1 EXIT: PASS.**
**External architect review: pending.** These pass results describe implementation
checks, not an ACCEPT decision from the AI that authored the specifications.

The user authorized continuing across tickets without waiting for individual
review turns. That superseded the packet's one-ticket-per-session workflow for
this implementation run. Ticket changes remain separated in Git so they can be
reviewed individually. Do not infer that every intermediate commit was externally
accepted, or that every intermediate commit was tested in an isolated checkout:
some checks ran alongside later, uncommitted ticket work. The final accumulated
tree was tested as a whole.

## Inputs and interpretation

- `Cognition_Codex_Kickoff_COG-0001.pdf`: initial repository foundation.
- `Cognition_Codex_Master_Implementation_Packet_Phase0-1.pdf`: ticket scope and gates.
- The later pasted academic-level Cognition specification: architectural guidance
  and normative Event, Wake, CognitionDecision, and Portable Manifest appendices.
- Earlier detailed ticket prompts: supplemental contract detail.

The original documents are not embedded in this repository. The architect who
provided them should compare this implementation against their authoritative
copies. Routine unspecified details were resolved within the architecture and
recorded in [implementation decisions](implementation-decisions.md).

## What is implemented

| Ticket | Commit | Deliverable |
| --- | --- | --- |
| COG-0001 | `4dfc3f2` | Python package boundaries and development tools |
| COG-0002 | `98a950f` | IDs, references, UTC clocks |
| COG-0003 | `df12976` | Event, Observation, Wake contracts |
| COG-0004 | `985baab` | Typed cognition decision and model interface contracts |
| COG-0005 | `1816bf1` | Capability, grant, binding, action, portable manifest contracts |
| COG-0006 | `5a2726c` | TOML configuration and sanitized canonical hashing |
| COG-0007 | `2eddc3e` | Scripted model, connector, world, provider, and fault fakes |
| COG-0008 | `fc7cf78` | Golden compatibility, offline and architecture checks |
| COG-0101 | `12f2ce2` | PostgreSQL sessions, schema checks, explicit Alembic foundation |
| COG-0102 | `eae232d` | Identity, governance, configuration, runtime and wake schema |
| COG-0103 | `dcaca3e` | Separate event metadata, content, administrative audit |
| COG-0104 | `2ad53ad` | Caller-transaction stores and concurrent wake coalescing |
| COG-0105 | `491cbd3` | Atomic birth, genesis evidence, bootstrap wake |
| COG-0106 | `5e049f4` | Dedicated-session singleton ownership and runtime observations |
| COG-0108 | `c8fb555` | Atomic configuration revision and evidence recording |
| COG-0107 | `8cfa7d0` | Local authenticated, audited lifecycle controls |
| COG-0109 | `e974b9c` | Structured read-only integrity checker and CLI |
| COG-0110 | `0af937b` | Crash/restart acceptance gate and final review corrections |

COG-0108 intentionally precedes COG-0107 in the packet's dependency order.
The initial repository commit is `dd4dddd`. Use `git show <commit>` for an
individual ticket or `git diff dd4dddd 0af937b` for the complete implementation.

## Evidence and review findings

The original final gate passed **712 tests**, including **16 acceptance cases**
covering all ten required recovery scenarios. Verification used real PostgreSQL
18 on Windows, Python 3.13, and isolated schemas. Full Ruff, strict source mypy,
and Git whitespace checks passed. [Upload verification](review/upload-verification.txt)
records the fresh checks performed for this GitHub handoff.

Recovery scenarios include all birth insert failure boundaries, duplicate runtime
startup, connection death, actual owned-subprocess death, pause and quiesce across
restarts, configuration revision changes, event append/restart, content redaction,
and stale runtime observation cleanup. Each recovery checks integrity and scans
durable state for test credentials and secret canaries.

Independent agent review found two concrete issues, both fixed and regression tested:

1. Mutated typed configuration could bypass validation at a public store boundary.
   Configuration now validates a detached snapshot before hashing or SQL, even if
   a caller catches validation errors and commits its surrounding transaction.
2. `ON CONFLICT DO NOTHING` could bypass incoming wake cause validation during
   coalescing. Both insertion and merge now require an existing cause belonging
   to the individual and hold a key-share lock on that event.

An independent real-backend termination probe also verified that a lost ownership
connection cannot reconnect and that a successor can acquire ownership.

## Decisions needing architect attention

Review the explicit lifecycle transition matrix, local OS administrator identity,
initial governance blocks, event naming, sanitized configuration allowlist,
coalescing semantics, and required nullable protocol fields in
[implementation decisions](implementation-decisions.md). These are implemented
choices, not requests to silently revise the architecture.

Check the three explicit migrations against model metadata, the schema and golden
fixtures against the normative appendices, and the acceptance cases against the
packet's numbered criteria. A test passing does not establish contract fidelity
where a specification was interpreted incorrectly.

Docker was unavailable, so Compose was supplied but not executed. Windows local
authentication was tested; the POSIX UID path was not exercised on a POSIX host.
There is no live provider integration, production deployment, or empirical proof
of long-term agent continuity.

## Where work stops and what comes next

The executable product is a durable backend foundation with local administrative
and diagnostic commands. It is not yet a working conversational or autonomous AI.
There is no wake-processing loop, cognition cycle/turn persistence, live model
invocation, belief/goal/memory state engine, action executor, portable export/import
implementation, or UI. Some of these have contracts only.

The supplied packet explicitly reserves Phase 2 for a separate approved packet
after Phase 1 review. Please review this tree, issue ACCEPT / AMEND / REJECT with
concrete findings, and then supply the next bounded implementation tickets.
Preserve the existing identity/evidence/authority separation and migration history
when requesting amendments.

See [Phase 1 operations](phase1-operations.md) for usable birth, ownership,
configuration, administration, and diagnostic APIs. The README describes setup
and how to run the full PostgreSQL test suite.
