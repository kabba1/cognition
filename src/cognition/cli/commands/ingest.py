"""Authenticated, finite local fixture ingress with no source acknowledgement."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from cognition.cli.commands.admin import local_principal
from cognition.cli.database import configured_engine
from cognition.connectors.local_json import LocalJsonConnector
from cognition.db.locks import OwnershipLostError, OwnershipUnavailableError
from cognition.db.session import create_session_factory
from cognition.policy.governance import require_admin
from cognition.protocols.common import SystemClock
from cognition.runtime.context import RUNTIME_CONTRACT_VERSION
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.runtime.perception import ingest_once
from cognition.stores.connectors import load_connector_binding
from cognition.stores.governance import find_admin_principal


def add_ingest_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "ingest-once", help="Ingest one bounded local JSON fixture page"
    )
    parser.add_argument("--individual-id", type=UUID, required=True)
    parser.add_argument("--binding-id", type=UUID, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.set_defaults(handler=handle_ingest)


def handle_ingest(args: argparse.Namespace) -> int:
    try:
        principal = local_principal()
        engine = configured_engine()
        try:
            with create_session_factory(engine).begin() as session:
                require_admin(
                    find_admin_principal(
                        session,
                        args.individual_id,
                        authn_provider=principal.authn_provider,
                        subject=principal.subject,
                    )
                )
                binding = load_connector_binding(
                    session, args.individual_id, args.binding_id
                )
                if binding.adapter_id != "local_json_v1":
                    raise ValueError("Unsupported local ingress adapter")
            connector = LocalJsonConnector(args.source, source_id=binding.source_id)
            clock = SystemClock()
            with acquire_runtime_ownership(
                engine,
                args.individual_id,
                clock=clock,
                host_id=socket.gethostname(),
                process_id=os.getpid(),
                runtime_version=RUNTIME_CONTRACT_VERSION,
            ) as owner:
                result = ingest_once(owner, args.binding_id, connector, clock)
        finally:
            engine.dispose()
    except (
        ValueError,
        LookupError,
        PermissionError,
        SQLAlchemyError,
        OSError,
        subprocess.SubprocessError,
        OwnershipLostError,
        OwnershipUnavailableError,
    ):
        print(
            "Ingress failed; check local administrator access, source binding, "
            "fixture format, database schema, and runtime ownership.",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "created_count": len(result.created_event_ids),
                "duplicate_count": result.duplicate_count,
                "wake_count": len(result.wake_ids),
                "cursor_revision": result.cursor_revision,
                "receipt_event_id": None
                if result.receipt_event_id is None
                else str(result.receipt_event_id),
            },
            sort_keys=True,
        )
    )
    return 0
