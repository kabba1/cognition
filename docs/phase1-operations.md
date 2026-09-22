# Phase 1 operations

Apply reviewed migrations explicitly with `alembic upgrade head` against the
database selected by `COGNITION_DATABASE_URL`. Runtime services do not create or
migrate databases. Never put a connection URL or password in version control.

Birth is a Python service; the local CLI exposes administration and diagnostics.
Start from the complete TOML shape in `tests/fixtures/config/valid.toml`, replacing
deployment settings and the individual UUID for your installation. Only the typed
behavior subset is stored in configuration history. The following example uses
the authenticated local OS identity as the initial administrator:

```python
from cognition.cli.commands.admin import local_principal
from cognition.cli.database import configured_engine
from cognition.config.loader import load_config
from cognition.db.session import create_session_factory
from cognition.protocols.common import SystemClock
from cognition.runtime.birth import BirthInput, birth

config = load_config("config.toml")
principal = local_principal()
engine = configured_engine()
try:
    result = birth(
        create_session_factory(engine),
        BirthInput(
            individual_id=config.runtime.individual_id,
            birth_name="Your chosen name",
            founding_orientation="Your explicit founding orientation",
            creator_provenance={"method": "local operator"},
            admin_authn_provider=principal.authn_provider,
            admin_subject=principal.subject,
            config=config,
            runtime_version="0.1.0",
        ),
        SystemClock(),
    )
    print(result.individual_id)
finally:
    engine.dispose()
```

Birth creates identity, governance, administrator, active configuration, genesis
event, event content, and a bootstrap wake atomically. A duplicate identity is an
error; retrying after a rolled-back transaction creates one complete birth.
External actions start blocked. Birth does not call a model or invent memories.

Runtime ownership is a bounded context manager, available for a future runtime:

```python
import os
import socket

from cognition.runtime.ownership import acquire_runtime_ownership

with acquire_runtime_ownership(
    engine,
    individual_id,
    clock=SystemClock(),
    host_id=socket.gethostname(),
    process_id=os.getpid(),
    runtime_version="0.1.0",
) as owner:
    print(owner.runtime_instance_id)
```

The engine in this second example must still be open. Holding ownership does not
run cognition or change the individual's lifecycle status. A separate pure helper,
`cognition.runtime.lifecycle.is_runnable(status)`, permits only `active`. Pause and
quiesce remain durable across ownership loss and reacquisition.

`cognition.config.recording.reconcile_config(factory, individual_id, config, clock)`
records changed behavior and its event together; identical behavior writes nothing.
The API validates the target identity and excludes deployment paths and secret
environment values. Direct store calls belong inside a caller-owned transaction.

`cognition check` reports structural inconsistencies without repairing them.
`cognition admin` supports pause, resume, begin/complete/abort quiesce, retirement,
and emergency external-action blocking. Aborted quiescence returns to paused;
retirement requires paused or quiescent state. Resume does not remove a governance
block. All successful administrative operations have an audit row and evidence.

Phase 1 provides a durable foundation. It does not yet provide a conversational
agent, wake-processing loop, goals and memories, provider integration, or deployment.
