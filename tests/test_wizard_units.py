"""Direct unit tests for the pure helpers behind the init wizard.

Unlike test_cli.py (subprocess e2e), these import functions directly because
the wizard's prompt layer cannot be driven through a non-TTY subprocess.
"""

from __future__ import annotations

import pytest
import yaml

from agensuite.cli import _set_sprint_frontmatter

SPRINT = (
    "---\n"
    "id: sprint-1\n"
    "title: Core MVP Definition\n"
    "participants: [cpo, cto, cdo, cco]\n"
    "debate_rounds: 2\n"
    "approval_quorum: 2\n"
    "---\n"
    "# Sprint 1: Core MVP Definition\n"
    "\n"
    "## Core Product Idea\n"
    "Trading platform.\n"
)


def test_updates_three_fields_and_preserves_body() -> None:
    out = _set_sprint_frontmatter(
        SPRINT, rounds=3, quorum=1, participants=["cpo", "cto"]
    )
    front, body = out.split("---\n", 2)[1], out.split("---\n", 2)[2]
    meta = yaml.safe_load(front)
    assert meta["debate_rounds"] == 3
    assert meta["approval_quorum"] == 1
    assert meta["participants"] == ["cpo", "cto"]
    # Untouched frontmatter keys survive.
    assert meta["id"] == "sprint-1"
    assert meta["title"] == "Core MVP Definition"
    # Markdown body is preserved verbatim.
    assert "## Core Product Idea" in body
    assert "Trading platform." in body


def test_rejects_quorum_above_participant_count() -> None:
    with pytest.raises(ValueError, match="quorum"):
        _set_sprint_frontmatter(
            SPRINT, rounds=2, quorum=3, participants=["cpo", "cto"]
        )


def test_rejects_quorum_below_one() -> None:
    with pytest.raises(ValueError, match="quorum"):
        _set_sprint_frontmatter(SPRINT, rounds=2, quorum=0, participants=["cpo"])


def test_rejects_rounds_below_one() -> None:
    with pytest.raises(ValueError, match="rounds"):
        _set_sprint_frontmatter(SPRINT, rounds=0, quorum=1, participants=["cpo"])
