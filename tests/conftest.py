"""Shared pytest fixtures.

The whole suite operates on isolated tmp directories — no test ever touches
the host's git config or workspace. ``project_root`` returns a fresh
directory containing only the bits a sprint loop needs (``sprints/`` with
one or two minimal sprint definitions). Each test that mutates state gets
its own ``project_root`` so parallelism is safe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """A fresh project directory with a minimal one-sprint setup."""
    (tmp_path / "sprints").mkdir()
    (tmp_path / "sprints" / "s.md").write_text(
        "---\n"
        "id: s\n"
        "title: Test sprint\n"
        "participants: [a, b, c]\n"
        "debate_rounds: 2\n"
        "approval_quorum: 1\n"
        "---\n"
        "test sprint body\n"
    )
    return tmp_path


@pytest.fixture()
def cli_env(project_root: Path) -> dict[str, str]:
    """Environment block that points the CLI at ``project_root``."""
    env = os.environ.copy()
    env["AGENTEAM_ROOT"] = str(project_root)
    # PYTHONPATH already covers src/ when the package is installed via `pip
    # install -e .`; for ad-hoc test runs we point at the in-tree src layout.
    project_src = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(project_src) + os.pathsep + env.get("PYTHONPATH", "")
    return env


@pytest.fixture()
def cli(project_root: Path, cli_env: dict[str, str]):
    """Callable that shells out to the CLI and returns the completed process."""

    def _run(*args: str, expect_ok: bool = True) -> subprocess.CompletedProcess[str]:
        p = subprocess.run(
            [sys.executable, "-m", "agenteam.cli", *args],
            cwd=str(project_root),
            env=cli_env,
            capture_output=True,
            text=True,
        )
        if expect_ok and p.returncode != 0:
            pytest.fail(
                f"agenteam {' '.join(args)} exited {p.returncode}\n"
                f"stdout: {p.stdout}\nstderr: {p.stderr}"
            )
        return p

    return _run
