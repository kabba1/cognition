"""Owned acceptance subprocess: acquire authority, report readiness, await death."""

import json
import os
import sys
from datetime import datetime
from uuid import UUID

from cognition.db.session import create_db_engine
from cognition.runtime.ownership import acquire_runtime_ownership
from cognition.testing.clock import FakeClock


def main():
    engine = create_db_engine(
        os.environ["COGNITION_ACCEPTANCE_DB_URL"],
        schema=os.environ["COGNITION_ACCEPTANCE_SCHEMA"],
    )
    owner = None
    try:
        owner = acquire_runtime_ownership(
            engine,
            UUID(os.environ["COGNITION_ACCEPTANCE_INDIVIDUAL_ID"]),
            clock=FakeClock(
                datetime.fromisoformat(os.environ["COGNITION_ACCEPTANCE_TIME"])
            ),
            host_id="acceptance-child",
            process_id=os.getpid(),
            runtime_version="phase1-acceptance",
        )
        print(
            json.dumps(
                {
                    "status": "ready",
                    "runtime_instance_id": str(owner.runtime_instance_id),
                    "backend_pid": owner.backend_pid,
                }
            ),
            flush=True,
        )
        # Pipe input is a deterministic blocking gate, with no sleep or polling.
        # The parent forcibly terminates this process while it holds authority.
        sys.stdin.read(1)
    except Exception as error:
        print(
            json.dumps({"status": "error", "error_type": type(error).__name__}),
            flush=True,
        )
        return 1
    finally:
        if owner is not None:
            owner.close()
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
