"""Dedicated PostgreSQL session advisory locks for individual ownership."""

import hashlib
import re
from types import TracebackType
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import DBAPIError, DontWrapMixin
from sqlalchemy.pool import ConnectionPoolEntry, NullPool


class OwnershipUnavailableError(RuntimeError):
    """Another PostgreSQL session already owns the individual."""


class OwnershipLostError(RuntimeError, DontWrapMixin):
    """The ownership connection was closed or invalidated; never reconnect it."""


def advisory_lock_key(individual_id: UUID) -> int:
    """Stable signed bigint using all UUID bytes and a versioned namespace.

    As with any 128-to-64-bit mapping, collisions are possible; a collision fails
    closed by excluding an additional individual rather than widening authority.
    """
    digest = hashlib.sha256(
        b"cognition:individual-ownership:v1:" + individual_id.bytes
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class AdvisoryOwnership:
    """A lifetime session lock on a physical connection that is never pooled."""

    def __init__(
        self, engine: Engine, connection: Connection, key: int, backend_pid: int
    ) -> None:
        self._engine = engine
        self._connection = connection
        self._key = key
        self.backend_pid = backend_pid
        self._closed = False

    @property
    def is_open(self) -> bool:
        """Check local connection state; remote loss surfaces on the next SQL call."""
        return not (
            self._closed or self._connection.closed or self._connection.invalidated
        )

    @property
    def connection(self) -> Connection:
        """Use the same lifetime session; invalidation must never reconnect it."""
        if not self.is_open:
            raise OwnershipLostError(
                "The dedicated ownership connection is no longer open"
            )
        return self._connection

    def close(self) -> None:
        """Explicitly unlock before physically closing; repeated close is harmless."""
        if self._closed:
            return
        self._closed = True
        connection = self._connection
        try:
            if not connection.closed and not connection.invalidated:
                try:
                    if connection.in_transaction():
                        connection.rollback()
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": self._key}
                    )
                    connection.commit()
                except DBAPIError as error:
                    # A dead physical session has already lost all session locks.
                    if not error.connection_invalidated:
                        raise
        finally:
            try:
                connection.close()
            finally:
                self._engine.dispose()

    def __enter__(self) -> "AdvisoryOwnership":
        _ = self.connection
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def acquire_individual_lock(engine: Engine, individual_id: UUID) -> AdvisoryOwnership:
    """Fail fast unless this dedicated session acquires the individual lock.

    Clone the connection URL and the schema selection exposed by create_db_engine.
    NullPool guarantees even an external Connection.close() closes the physical
    session, unlike returning a session lock to a regular application's pool.
    """
    schema = engine.get_execution_options().get("cognition_schema")
    options = "-ctimezone=UTC"
    if schema is not None:
        if (
            not isinstance(schema, str)
            or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) is None
        ):
            raise ValueError("Invalid cognition_schema execution option")
        options += f" -csearch_path={schema},pg_catalog"
    dedicated = create_engine(
        engine.url,
        poolclass=NullPool,
        connect_args={"options": options},
        hide_parameters=True,
    )
    if schema is not None:
        dedicated.update_execution_options(cognition_schema=schema)
    physical_connection_created = False

    @event.listens_for(dedicated, "connect")
    def forbid_reconnect(
        dbapi_connection: DBAPIConnection, connection_record: ConnectionPoolEntry
    ) -> None:
        nonlocal physical_connection_created
        if physical_connection_created:
            dbapi_connection.close()
            raise OwnershipLostError("An ownership session must never reconnect")
        physical_connection_created = True

    connection: Connection | None = None
    try:
        connection = dedicated.connect()
        key = advisory_lock_key(individual_id)
        acquired, backend_pid = connection.execute(
            text("SELECT pg_try_advisory_lock(:key), pg_backend_pid()"), {"key": key}
        ).one()
        connection.commit()
        if not acquired:
            raise OwnershipUnavailableError(
                "The individual already has an active owner"
            )
        return AdvisoryOwnership(dedicated, connection, key, backend_pid)
    except BaseException:
        if connection is not None:
            connection.close()
        dedicated.dispose()
        raise
