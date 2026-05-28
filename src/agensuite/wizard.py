"""Interactive setup wizard for ``agensuite init``.

This module is the ONLY place that imports ``questionary``. Everything the
wizard collects is returned as an ``InitAnswers`` model; the CLI applies it
through pure functions so the file-mutation logic stays prompt-free and
unit-testable.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Spoke roles the user can tune. CEO is included for biases but is never a
# sprint participant (it composes ADRs / next sprints, it does not draft).
SPOKES = ["cpo", "cto", "cdo", "cco"]
PERSONAS = ["ceo", *SPOKES]

# Short ownership blurbs shown next to each persona in the wizard.
PERSONA_BLURBS = {
    "ceo": "orchestrator — ADRs & next-sprint authoring",
    "cpo": "product, UX & success metrics",
    "cto": "infra, latency & cost",
    "cdo": "data entities, ingestion & retention",
    "cco": "risk, privacy & compliance",
}


class InitAnswers(BaseModel):
    """Everything the wizard collects, ready to apply to scaffolded files."""

    idea: str
    biases: dict[str, list[str]] = Field(default_factory=dict)
    debate_rounds: int = 2
    approval_quorum: int = 2
    participants: list[str] = Field(default_factory=lambda: list(SPOKES))


def default_answers(idea: str) -> InitAnswers:
    """Non-interactive answers: the given idea plus every default."""
    return InitAnswers(idea=idea)
