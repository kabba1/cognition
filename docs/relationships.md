# Relationship substrate

Relationships connect an owned entity to a subjective narrative and evidence.
They have no scalar trust, friendship or emotion score. Interaction history stays
in the evidence ledger, while existing beliefs and commitments can refer to the
same entity. A directory entry or social interpretation grants no administrative
or capability authority.

The trusted Python store APIs in `cognition.stores.relationships` are
`create_relationship`, `revise_relationship`, `create_relationship_thread` and
`revise_relationship_thread`. They acquire the shared individual lock, validate
ownership and join the caller's transaction. Current rows, exact before/after
revisions and evidence events commit together. They do not send messages or
independently commit. Birth creates neither relationships nor threads.

There is one relationship per individual/entity pair. Its entity is immutable;
revising the narrative cannot move the relationship's history to another entity.
A narrative requires nonblank content/rationale and at least one owned evidence
reference. A revised claim receives its supplied support; the previous claim and
support remain in history. An owned reference is provenance, not proof of truth.

Threads retain open topics with title, summary, rationale and evidence. They start
open and may be revised, resolved or abandoned. Terminal threads cannot reopen or
be revised; a later follow-up is a distinct thread. Their relationship is immutable.
An optional owned commitment link can change while open, but this does not fulfill,
release or alter the commitment. Null revise arguments mean unchanged.

Stores reject foreign/missing links, blank content, unsupported transitions,
unchanged revisions, retrograde revision times and conflicting unflushed personal
state. Diagnostics additionally inspect owned links, nonempty evidence, exact
revision chains/current projections, and unchanged parent identity across history.

Context reads at most eight relationships and eight open threads, plus bounded
projects and entity descriptions. Relationship sections render the associated
entity and reference both records. Thread sections reference only the rendered
thread; nested relationship/commitment IDs are pointers, not claims that those
other records were retrieved. All sections still compete within the global context
budget. Reading never updates the relationship or strengthens an interpretation.

Decision v1 can cite existing owned `relationship` and `relationship_thread`
references in supported operations, including focus and self-scheduled wakes.
It cannot create or revise entities, projects, relationships or threads. Those
model-authored operations require the planned explicit executive protocol extension.
Entity identifier binding, entity merging and goal dependencies remain later work.
