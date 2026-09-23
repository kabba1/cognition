"""Script inbound delivery independently of Cognition observation persistence."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from cognition.connectors.base import ConnectorBatch as ConnectorBatch
from cognition.connectors.base import ConnectorItem as ConnectorItem
from cognition.connectors.base import MalformedConnectorItem as MalformedConnectorItem


@dataclass(frozen=True)
class ConnectorPollStep:
    """The expected input cursor and deterministic outcome of one poll."""

    expected_cursor: str | None
    outcome: ConnectorBatch | Exception


class ConnectorScriptExhausted(RuntimeError):
    """No more source outcomes were configured."""


class FakeConnector:
    """A cursor-sensitive source script, without deduplication or persistence."""

    def __init__(self, steps: Sequence[ConnectorPollStep]) -> None:
        # Exceptions may require constructor-only arguments and are not generally
        # deepcopyable. Copy source data while preserving configured error types.
        self._steps = tuple(
            ConnectorPollStep(
                step.expected_cursor,
                step.outcome
                if isinstance(step.outcome, Exception)
                else deepcopy(step.outcome),
            )
            for step in steps
        )
        self._position = 0
        self._polls: list[str | None] = []
        self._acknowledgements: list[str | None] = []

    @property
    def polls(self) -> tuple[str | None, ...]:
        """All supplied cursors, including failed and mismatched polls."""
        return tuple(self._polls)

    @property
    def acknowledgements(self) -> tuple[str | None, ...]:
        """Requested cursor updates; polling never implies acknowledgement."""
        return tuple(self._acknowledgements)

    def poll(self, cursor: str | None) -> ConnectorBatch:
        """Deliver the next scripted page or failure without reordering items."""
        self._polls.append(cursor)
        if self._position >= len(self._steps):
            raise ConnectorScriptExhausted("connector script exhausted")
        step = self._steps[self._position]
        if cursor != step.expected_cursor:
            raise AssertionError(
                f"expected cursor {step.expected_cursor!r}, received {cursor!r}"
            )
        self._position += 1
        if isinstance(step.outcome, Exception):
            raise step.outcome
        return deepcopy(step.outcome)

    def acknowledge(self, cursor: str | None) -> None:
        """Record an explicit acknowledgement without persisting domain state."""
        self._acknowledgements.append(cursor)
