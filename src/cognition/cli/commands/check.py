"""Local, read-only database integrity diagnostics with structured output."""

from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from cognition.cli.database import require_supported_schema
from cognition.db.checks import check_database
from cognition.db.session import create_db_engine, create_session_factory


def add_check_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser("check", help="Report Phase 1 database integrity")
    parser.set_defaults(handler=handle_check)


def handle_check(namespace: argparse.Namespace) -> int:
    """Return 0 for healthy, 1 for findings, and 2 for unavailable diagnostics."""
    url = os.environ.get("COGNITION_DATABASE_URL")
    if not url:
        print("Set COGNITION_DATABASE_URL before running check.", file=sys.stderr)
        return 2
    engine: Engine | None = None
    try:
        engine = create_db_engine(url)
        require_supported_schema(engine)
        factory = create_session_factory(engine)
        with factory.begin() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            report = check_database(session)
    except (SQLAlchemyError, ValueError):
        # Driver errors can include connection URLs or provider details.
        print("Database integrity check could not complete.", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()
    print(
        json.dumps(
            {
                "healthy": report.healthy,
                "findings": [
                    {
                        "invariant_id": finding.invariant_id,
                        "severity": finding.severity,
                        "subject": finding.subject.model_dump(mode="json"),
                        "message": finding.message,
                    }
                    for finding in report.findings
                ],
                "not_applicable": list(report.not_applicable),
            },
            sort_keys=True,
        )
    )
    return 0 if report.healthy else 1
