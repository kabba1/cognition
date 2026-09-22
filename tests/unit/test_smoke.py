"""Smoke tests for the installed package and CLI."""

import subprocess
import sys
from pathlib import Path


def test_package_import(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import cognition"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_help(tmp_path: Path) -> None:
    command = [sys.executable, "-I", "-m", "cognition.cli.main", "--help"]
    first = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, check=False
    )
    second = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, check=False
    )

    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert "usage: cognition" in first.stdout
    assert "--help" in first.stdout
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr == ""
