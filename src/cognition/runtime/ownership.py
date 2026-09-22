"""Synchronous runtime ownership lifetime without a daemon or wake loop."""

from types import TracebackType
from uuid import UUID

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from cognition.db.locks import AdvisoryOwnership, acquire_individual_lock
from cognition.protocols.common import Clock
from cognition.stores.runtime import record_runtime_start, record_runtime_stop


class RuntimeOwnership:
    """Keep advisory authority alive independently from observational row state."""

    def __init__(
        self,
        lock: AdvisoryOwnership,
        runtime_instance_id: UUID,
        clock: Clock,
        individual_id: UUID,
    ) -> None:
        self._lock = lock
        self.runtime_instance_id = runtime_instance_id
        self.individual_id = individual_id
        self._clock = clock
        self._closed = False

    @property
    def connection(self) -> Connection:
        return self._lock.connection

    @property
    def backend_pid(self) -> int:
        return self._lock.backend_pid

    def close(self) -> None:
        """Record graceful stop when possible, then always relinquish authority."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._lock.is_open:
                try:
                    # Do not join an incidental caller transaction: its rollback
                    # must not also roll back our graceful-stop observation.
                    if self.connection.in_transaction():
                        self.connection.rollback()
                    with Session(bind=self.connection) as session, session.begin():
                        record_runtime_stop(
                            session, self.runtime_instance_id, now=self._clock.now()
                        )
                except DBAPIError as error:
                    # Leave an unclosed row for the next owner to mark crashed.
                    if not error.connection_invalidated:
                        raise
        finally:
            self._lock.close()

    def __enter__(self) -> "RuntimeOwnership":
        _ = self.connection
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def acquire_runtime_ownership(
    engine: Engine,
    individual_id: UUID,
    *,
    clock: Clock,
    host_id: str,
    process_id: int,
    runtime_version: str,
) -> RuntimeOwnership:
    """Acquire first, then atomically update startup observations on that session."""
    lock = acquire_individual_lock(engine, individual_id)
    try:
        with Session(bind=lock.connection) as session, session.begin():
            instance = record_runtime_start(
                session,
                individual_id,
                now=clock.now(),
                host_id=host_id,
                process_id=process_id,
                runtime_version=runtime_version,
            )
        return RuntimeOwnership(
            lock, instance.runtime_instance_id, clock, individual_id
        )
    except BaseException:
        lock.close()
        raise
