"""Bounded PostgreSQL English recall over owned personal and visible event text."""

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from itertools import islice, zip_longest
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Integer,
    Text,
    cast,
    column,
    func,
    literal,
    literal_column,
    select,
    values,
)
from sqlalchemy.dialects.postgresql import REGCONFIG, TSVECTOR
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from cognition.db.models.evidence import Event, EventContent
from cognition.db.models.personal import Belief, Episode
from cognition.db.search import BELIEF_VECTOR_SQL, EPISODE_VECTOR_SQL, EVENT_VECTOR_SQL
from cognition.domain.attention import (
    LEXICAL_DICTIONARY,
    LEXICAL_MATCH_LIMIT,
    LEXICAL_RAW_TERM_LIMIT,
    LEXICAL_SOURCE_CHAR_LIMIT,
    LEXICAL_SOURCE_TOKEN_LIMIT,
    LEXICAL_TERM_LIMIT,
    LEXICAL_TOKEN_CHAR_LIMIT,
    LEXICAL_WAKE_LIMIT,
)
from cognition.protocols.cognition_v1 import CurrentFocus
from cognition.protocols.events_v1 import EventEnvelopeV1
from cognition.protocols.model_v1 import ContextSection
from cognition.protocols.wakes_v1 import WakeV1
from cognition.stores.evidence import StoredEvent
from cognition.stores.personal import personal_context_section


@dataclass(frozen=True)
class LexicalMatch:
    content: ContextSection | StoredEvent
    rank: float


@dataclass(frozen=True)
class LexicalSelection:
    matches: tuple[LexicalMatch, ...] = ()
    query_terms: tuple[str, ...] = ()
    query_sources: tuple[str, ...] = ()
    effective_query: str = ""


def _source_words(text: str) -> list[str]:
    prefix = text[:LEXICAL_SOURCE_CHAR_LIMIT]
    cut_word = len(text) > len(prefix) and text[len(prefix)].isalnum()
    words: list[str] = []
    for match in islice(re.finditer(r"[^\W_]+", prefix), LEXICAL_SOURCE_TOKEN_LIMIT):
        if cut_word and match.end() == len(prefix):
            continue
        word = match.group()
        if len(word) <= LEXICAL_TOKEN_CHAR_LIMIT:
            words.append(word)
    return words


def _raw_query_terms(
    individual_id: UUID, wakes: Sequence[WakeV1], focus: CurrentFocus | None
) -> list[tuple[str, str]]:
    sources = [] if focus is None else [(focus.summary, "focus")]
    ordered = sorted(
        (wake for wake in wakes if wake.individual_id == individual_id),
        key=lambda wake: (wake.due_at, wake.wake_id.int),
    )[:LEXICAL_WAKE_LIMIT]
    sources.extend((wake.purpose, f"wake:{wake.wake_id}") for wake in ordered)
    columns = [
        [(word, origin) for word in _source_words(text)] for text, origin in sources
    ]
    seen: set[str] = set()
    terms: list[tuple[str, str]] = []
    for round_terms in zip_longest(*columns):
        for term in round_terms:
            if term is None or term[0] in seen:
                continue
            seen.add(term[0])
            terms.append(term)
            if len(terms) == LEXICAL_RAW_TERM_LIMIT:
                return terms
    return terms


def _stored_event(row: Mapping[str, Any]) -> StoredEvent:
    envelope = EventEnvelopeV1.model_validate(
        {
            "schema_version": row["schema_version"],
            "event_id": row["event_id"],
            "individual_id": row["individual_id"],
            "event_type": row["event_type"],
            "occurred_at": row["occurred_at"],
            "observed_at": row["observed_at"],
            "recorded_at": row["recorded_at"],
            "source": {
                "kind": row["source_kind"],
                "source_id": row["source_id"],
                "binding_id": row["source_binding_id"],
            },
            "actor_entity_id": row["actor_entity_id"],
            "causation_event_id": row["causation_event_id"],
            "correlation_id": row["correlation_id"],
            "subject": None
            if row["subject_kind"] is None
            else {"kind": row["subject_kind"], "id": row["subject_id"]},
            "provenance": deepcopy(row["provenance"]),
            "runtime_version": row["runtime_version"],
            "content": {
                "content_type": row["content_type"],
                "payload": deepcopy(row["payload"]),
                "text": row["text"],
                "blob_ref": row["blob_ref"],
                "content_hash": row["content_hash"],
                "sensitivity": row["sensitivity"],
                "retention_class": row["retention_class"],
                "retain_until": row["retain_until"],
            },
        }
    )
    return StoredEvent(
        envelope, row["event_sequence"], row["redacted_at"], row["redaction_audit_id"]
    )


def retrieve_lexical_attention(
    session: Session,
    individual_id: UUID,
    *,
    wakes: Sequence[WakeV1],
    focus: CurrentFocus | None,
) -> LexicalSelection:
    """Return at most eight matches per corpus using bound, normalized query data.

    Limits describe the bounded returned pool, not total matching index entries
    or a global database work bound. All source columns are read without autoflush
    or ORM identity-map refresh; caller owns the coherent snapshot transaction.
    """
    raw = _raw_query_terms(individual_id, wakes, focus)
    if not raw:
        return LexicalSelection()
    dictionary = cast(literal(LEXICAL_DICTIONARY), REGCONFIG)
    bounded = (
        values(column("position", Integer), column("raw", Text))
        .data([(position, term) for position, (term, _) in enumerate(raw)])
        .alias("bounded_terms")
    )
    matches: list[LexicalMatch] = []
    retained: list[tuple[str, str]] = []
    normalized_seen: set[tuple[str, ...]] = set()
    surviving: dict[str, list[tuple[str, str]]] = {origin: [] for _, origin in raw}
    with session.no_autoflush:
        normalized = (
            session.execute(
                select(
                    bounded.c.position,
                    cast(func.plainto_tsquery(dictionary, bounded.c.raw), Text).label(
                        "query"
                    ),
                    func.tsvector_to_array(
                        func.to_tsvector(dictionary, bounded.c.raw)
                    ).label("lexemes"),
                ).order_by(bounded.c.position)
            )
            .mappings()
            .all()
        )
        for row in normalized:
            lexemes = tuple(sorted(row["lexemes"]))
            if not row["query"] or not lexemes or lexemes in normalized_seen:
                continue
            normalized_seen.add(lexemes)
            term = raw[row["position"]]
            surviving[term[1]].append(term)
        # Stopwords must not consume a source's fair opportunity. Keep each
        # normalized set's first raw origin, then allocate surviving terms again
        # in focus-first source rounds before the normalized cap is applied.
        for round_terms in zip_longest(*surviving.values()):
            for term in round_terms:
                if term is not None:
                    retained.append(term)
                if len(retained) == LEXICAL_TERM_LIMIT:
                    break
            if len(retained) == LEXICAL_TERM_LIMIT:
                break
        if not retained:
            return LexicalSelection()
        query: ColumnElement[Any] = func.plainto_tsquery(dictionary, retained[0][0])
        for term, _ in retained[1:]:
            query = query.op("||")(func.plainto_tsquery(dictionary, term))
        effective = session.scalar(select(cast(query, Text)))
        assert effective is not None

        for model, kind, vector_sql in (
            (Belief, "belief", BELIEF_VECTOR_SQL),
            (Episode, "episode", EPISODE_VECTOR_SQL),
        ):
            table = model.__table__
            vector = literal_column(vector_sql, type_=TSVECTOR)
            rank = func.ts_rank_cd(vector, query)
            statement = select(table, rank.label("lexical_rank")).where(
                table.c.individual_id == individual_id,
                vector.op("@@")(query),
            )
            if kind == "belief":
                statement = statement.where(
                    table.c.status.in_(("tentative", "accepted", "disputed"))
                )
            chronology = table.c.updated_at if kind == "belief" else table.c.created_at
            ranked = (
                session.execute(
                    statement.order_by(
                        rank.desc(), chronology.desc(), table.c[f"{kind}_id"]
                    ).limit(LEXICAL_MATCH_LIMIT)
                )
                .mappings()
                .all()
            )
            for row in ranked:
                stored = {field.name: row[field.name] for field in table.columns}
                matches.append(
                    LexicalMatch(
                        personal_context_section(kind, stored),
                        float(row["lexical_rank"]),
                    )
                )

        events, contents = Event.__table__, EventContent.__table__
        vector = literal_column(EVENT_VECTOR_SQL, type_=TSVECTOR)
        rank = func.ts_rank_cd(vector, query)
        ranked_events = (
            session.execute(
                select(
                    events,
                    *(field for field in contents.columns if field.name != "event_id"),
                    rank.label("lexical_rank"),
                )
                .join(contents, contents.c.event_id == events.c.event_id)
                .where(
                    events.c.individual_id == individual_id,
                    events.c.source_kind != "admin",
                    events.c.event_type != "individual.born",
                    contents.c.text.is_not(None),
                    contents.c.sensitivity.in_(("public", "internal")),
                    contents.c.redacted_at.is_(None),
                    vector.op("@@")(query),
                )
                .order_by(
                    rank.desc(), events.c.event_sequence.desc(), events.c.event_id
                )
                .limit(LEXICAL_MATCH_LIMIT)
            )
            .mappings()
            .all()
        )
        matches.extend(
            LexicalMatch(_stored_event(dict(row)), float(row["lexical_rank"]))
            for row in ranked_events
        )
    return LexicalSelection(
        tuple(matches),
        tuple(term for term, _ in retained),
        tuple(origin for _, origin in retained),
        effective,
    )
