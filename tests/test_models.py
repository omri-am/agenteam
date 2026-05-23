"""Unit tests for the Pydantic schemas in :mod:`agenteam.models`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agenteam.models import (
    DebateState,
    DecisionRecord,
    PullRequest,
    SprintConfig,
)


class TestSprintConfigValidation:
    def test_empty_participants_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SprintConfig(
                id="x", title="t", participants=[],
                debate_rounds=1, approval_quorum=1,
            )

    def test_duplicate_participants_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SprintConfig(
                id="x", title="t", participants=["a", "a"],
                debate_rounds=1, approval_quorum=1,
            )

    def test_quorum_above_participant_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SprintConfig(
                id="x", title="t", participants=["a"],
                debate_rounds=1, approval_quorum=2,
            )

    def test_debate_rounds_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SprintConfig(
                id="x", title="t", participants=["a", "b"],
                debate_rounds=0, approval_quorum=1,
            )


class TestDebateStateBuild:
    def _sprint(self, rounds: int = 2) -> SprintConfig:
        return SprintConfig(
            id="s", title="t", participants=["a", "b", "c"],
            debate_rounds=rounds, approval_quorum=1,
        )

    def _pr(self, pid: str, author: str) -> PullRequest:
        return PullRequest(
            id=pid, title=pid, branch=f"feat/{author}", author=author, sprint_id="s",
        )

    def test_skips_self_review(self) -> None:
        sprint = self._sprint(rounds=1)
        prs = [self._pr("pr-a", "a"), self._pr("pr-b", "b")]
        state = DebateState.build(sprint, prs)
        # Each PR is reviewed by 2 non-authors; no self-review slots.
        assert len(state.schedule) == 4
        for turn in state.schedule:
            pr_author = next(p.author for p in prs if p.id == turn.target_pr_id)
            assert turn.speaker != pr_author

    def test_round_ordering_is_contiguous(self) -> None:
        """All round-0 turns come before any round-1 turn."""
        sprint = self._sprint(rounds=2)
        prs = [self._pr("pr-a", "a"), self._pr("pr-b", "b")]
        state = DebateState.build(sprint, prs)
        rounds = [t.round_idx for t in state.schedule]
        # Strictly non-decreasing — round_idx never goes backwards.
        assert rounds == sorted(rounds)

    def test_cursor_helpers(self) -> None:
        sprint = self._sprint(rounds=1)
        prs = [self._pr("pr-a", "a"), self._pr("pr-b", "b")]
        state = DebateState.build(sprint, prs)

        assert state.current_turn() is not None
        assert not state.is_finished

        for _ in range(len(state.schedule)):
            state.advance()

        assert state.is_finished
        assert state.current_turn() is None
        # Advance past the end is idempotent.
        state.advance()
        assert state.cursor == len(state.schedule)


class TestDecisionRecordMarkdown:
    def _record(self, **overrides) -> DecisionRecord:
        kwargs = dict(
            id="ADR-001", title="Pick X", sprint_id="s",
            context="ctx", options=["A", "B"], decision="B",
            consequences="faster", signoffs=["ceo"],
        )
        kwargs.update(overrides)
        return DecisionRecord(**kwargs)

    def test_default_status_is_accepted(self) -> None:
        md = self._record().to_markdown()
        assert "Status:** Accepted" in md

    def test_status_field_is_rendered(self) -> None:
        md = self._record(status="Rejected").to_markdown()
        assert "Status:** Rejected" in md
        assert "Status:** Accepted" not in md

    def test_empty_sections_render_placeholder(self) -> None:
        md = self._record(
            context="", options=[], decision="", consequences="", signoffs=[],
        ).to_markdown()
        # Five sections (Context, Options, Decision, Consequences) plus the
        # Signoffs header line — all should fall back to the placeholder.
        assert md.count("_None recorded._") >= 4

    def test_options_rendered_as_bullets(self) -> None:
        md = self._record(options=["First", "Second"]).to_markdown()
        assert "- First" in md
        assert "- Second" in md
