"""Local OS authenticated administrative CLI; no social input path."""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from cognition.cli.database import configured_engine
from cognition.db.session import create_session_factory
from cognition.policy.governance import ADMIN_OPERATIONS, AuthenticatedPrincipal
from cognition.protocols.common import SystemClock
from cognition.runtime.exploration_admin import (
    EXPLORATION_ADMIN_OPERATIONS,
    ExplorationAdminResult,
    set_internal_exploration,
)
from cognition.runtime.lifecycle import AdminOperationResult, apply_admin_operation


def local_principal() -> AuthenticatedPrincipal:
    """Resolve the actual process token; environment usernames are not identity."""
    if os.name != "nt":
        get_uid = getattr(os, "getuid", None)
        if not callable(get_uid):
            raise OSError(
                "This operating system has no supported local identity source"
            )
        return AuthenticatedPrincipal("local_os", f"uid:{get_uid()}")
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("Cannot locate the Windows system directory")
    result = subprocess.run(
        [str(Path(buffer.value) / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    rows = list(csv.reader(result.stdout.splitlines()))
    if (
        len(rows) != 1
        or len(rows[0]) != 2
        or not re.fullmatch(r"S-\d+(?:-\d+)+", rows[0][1])
    ):
        raise PermissionError("Cannot establish the current Windows user SID")
    return AuthenticatedPrincipal("local_os", "sid:" + rows[0][1])


def add_admin_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "admin", help="Apply a local administrative operation"
    )
    parser.add_argument(
        "operation", choices=(*ADMIN_OPERATIONS, *EXPLORATION_ADMIN_OPERATIONS)
    )
    parser.add_argument("--individual-id", type=UUID, required=True)
    parser.add_argument("--reason", required=True)
    parser.set_defaults(handler=handle_admin)


def handle_admin(args: argparse.Namespace) -> int:
    try:
        principal = local_principal()
        engine = configured_engine()
        try:
            result: AdminOperationResult | ExplorationAdminResult
            if args.operation in EXPLORATION_ADMIN_OPERATIONS:
                result = set_internal_exploration(
                    create_session_factory(engine),
                    args.individual_id,
                    principal,
                    args.operation == "enable_exploration",
                    args.reason,
                    SystemClock(),
                )
            else:
                result = apply_admin_operation(
                    create_session_factory(engine),
                    args.individual_id,
                    principal,
                    args.operation,
                    args.reason,
                    SystemClock(),
                )
        finally:
            engine.dispose()
    except (ValueError, LookupError, PermissionError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError, subprocess.SubprocessError):
        # Database exception representations can contain parameter values or URLs.
        print(
            "Administrative command failed; no successful result was recorded",
            file=sys.stderr,
        )
        return 2
    payload: dict[str, object] = {
        "individual_id": str(result.individual_id),
        "operational_status": result.operational_status,
        "audit_id": str(result.audit_id),
        "event_id": str(result.event_id),
    }
    if isinstance(result, ExplorationAdminResult):
        payload["internal_exploration_enabled"] = result.enabled
        payload["governance_revision"] = result.governance_revision
    print(json.dumps(payload, sort_keys=True))
    return 0
