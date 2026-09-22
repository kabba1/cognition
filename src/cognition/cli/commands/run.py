"""Explicit, locally authorized smoke execution with a JSON fixture adapter."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from cognition.cli.commands.admin import local_principal
from cognition.cli.database import configured_engine
from cognition.db.locks import OwnershipLostError, OwnershipUnavailableError
from cognition.db.models.cognition import CognitionCycle, CognitionTurn, ContextSnapshot
from cognition.db.session import create_session_factory
from cognition.models.script_file import load_script_file
from cognition.policy.governance import require_admin
from cognition.protocols.common import SystemClock
from cognition.runtime.cognition import CognitionRuntime
from cognition.runtime.context import RUNTIME_CONTRACT_VERSION
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.stores.configuration import get_active_config
from cognition.stores.governance import find_admin_principal


def add_run_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "run-once",
        help="Run one bounded cycle using local JSON decision fixtures",
    )
    parser.add_argument("--individual-id", type=UUID, required=True)
    parser.add_argument("--script", type=Path, required=True)
    parser.set_defaults(handler=handle_run)


def handle_run(args: argparse.Namespace) -> int:
    try:
        model = load_script_file(args.script)
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
                config = get_active_config(session, args.individual_id)
                if config is None or (
                    config.sanitized_config.model.adapter != "script-file"
                    or config.sanitized_config.model.requested_model != "script-file"
                ):
                    raise ValueError("Active model configuration must be script-file")
                incompatible_snapshot = session.scalar(
                    select(ContextSnapshot.snapshot_id)
                    .join(
                        CognitionTurn, ContextSnapshot.turn_id == CognitionTurn.turn_id
                    )
                    .join(
                        CognitionCycle,
                        CognitionTurn.cycle_id == CognitionCycle.cycle_id,
                    )
                    .where(
                        CognitionCycle.individual_id == args.individual_id,
                        CognitionCycle.status == "active",
                        CognitionTurn.status.in_(("prepared", "invoking")),
                        CognitionTurn.decision_id.is_(None),
                        or_(
                            ContextSnapshot.model_adapter != "script-file",
                            ContextSnapshot.requested_model != "script-file",
                        ),
                    )
                    .limit(1)
                )
                if incompatible_snapshot is not None:
                    raise ValueError("Frozen cycle configuration is not script-file")
            clock = SystemClock()
            with acquire_runtime_ownership(
                engine,
                args.individual_id,
                clock=clock,
                host_id=socket.gethostname(),
                process_id=os.getpid(),
                runtime_version=RUNTIME_CONTRACT_VERSION,
            ) as owner:
                result = CognitionRuntime(
                    owner,
                    args.individual_id,
                    model,
                    clock,
                    bound_model=("script-file", "script-file"),
                ).run_once()
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
        # Validation and DB errors may include raw inputs, credentials, or SQL.
        print(
            "Scripted run failed; check the script, active script-file configuration, "
            "local administrator access, database schema, and runtime ownership.",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "cycle_id": str(result.cycle_id) if result.cycle_id else None,
                "status": result.status,
                "reason": result.reason,
            },
            sort_keys=True,
        )
    )
    return 1 if result.status == "failed" else 0
