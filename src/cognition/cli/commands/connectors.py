"""Narrow local-OS authenticated connector registration and enablement CLI."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from cognition.cli.commands.admin import local_principal
from cognition.cli.database import configured_engine
from cognition.db.session import create_session_factory
from cognition.protocols.common import SystemClock
from cognition.runtime.connector_admin import register_connector, set_connector_enabled


def add_connectors_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "connector", help="Administer an inbound source binding"
    )
    operations = parser.add_subparsers(dest="connector_operation", required=True)
    for operation in ("register", "enable", "disable"):
        command = operations.add_parser(operation)
        command.add_argument("--individual-id", type=UUID, required=True)
        command.add_argument("--reason", required=True)
        if operation == "register":
            command.add_argument("--adapter", choices=("local_json_v1",), required=True)
            command.add_argument("--source-id", required=True)
            command.add_argument("--enabled", action="store_true")
        else:
            command.add_argument("--binding-id", type=UUID, required=True)
        command.set_defaults(handler=handle_connector)


def handle_connector(args: argparse.Namespace) -> int:
    try:
        principal = local_principal()
        engine = configured_engine()
        try:
            factory = create_session_factory(engine)
            if args.connector_operation == "register":
                result = register_connector(
                    factory,
                    args.individual_id,
                    principal,
                    adapter_id=args.adapter,
                    source_id=args.source_id,
                    enabled=args.enabled,
                    reason=args.reason,
                    clock=SystemClock(),
                )
            else:
                result = set_connector_enabled(
                    factory,
                    args.individual_id,
                    args.binding_id,
                    principal,
                    enabled=args.connector_operation == "enable",
                    reason=args.reason,
                    clock=SystemClock(),
                )
        finally:
            engine.dispose()
    except (ValueError, LookupError, PermissionError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError, subprocess.SubprocessError):
        print(
            "Connector administration failed; no successful result was recorded",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "individual_id": str(result.individual_id),
                "connector_binding_id": str(result.connector_binding_id),
                "enabled": result.enabled,
                "revision": result.revision,
                "audit_id": str(result.audit_id),
                "event_id": str(result.event_id),
            },
            sort_keys=True,
        )
    )
    return 0
