"""Run Phase 0 with connection attempts forbidden and provider env absent."""

import os
import subprocess
import sys
from pathlib import Path


def test_phase0_without_network_database_or_provider_credentials() -> None:
    environment = os.environ.copy()
    for key in list(environment):
        if (
            key.startswith(("OPENAI_", "ANTHROPIC_", "AWS_", "COGNITION_"))
            or key == "DATABASE_URL"
        ):
            del environment[key]
    script = """
import socket
import pytest
def forbidden(*args, **kwargs):
    raise AssertionError('Phase 0 attempted a network/database connection')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
raise SystemExit(pytest.main(['tests/unit', 'tests/contracts', '-q']))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
