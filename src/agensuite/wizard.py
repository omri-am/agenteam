"""Interactive setup wizard for ``agensuite init``.

This module is the ONLY place that imports ``questionary``. Everything the
wizard collects is returned as an ``InitAnswers`` model; the CLI applies it
through pure functions so the file-mutation logic stays prompt-free and
unit-testable.
"""

from __future__ import annotations

import questionary
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


def _required(answer: object) -> str:
    """Return a questionary answer, or abort cleanly if the user cancelled.

    ``questionary``'s ``.ask()`` returns ``None`` on Ctrl-C / EOF instead of
    raising. Treat that as an explicit abort of the whole setup.
    """
    if answer is None:
        raise SystemExit("setup cancelled")
    return str(answer)


def _ask_idea() -> str:
    while True:
        idea = _required(questionary.text("Describe your startup in one line:").ask()).strip()
        if idea:
            return idea
        questionary.print("  idea cannot be empty.", style="fg:red")


def _ask_biases() -> dict[str, list[str]]:
    biases: dict[str, list[str]] = {}
    for role in PERSONAS:
        tune = questionary.confirm(
            f"Tune the {role.upper()} ({PERSONA_BLURBS[role]})?",
            default=False,
        ).ask()
        if tune is None:
            raise SystemExit("setup cancelled")
        if not tune:
            continue
        lines: list[str] = []
        questionary.print(
            "  Enter one bias per line; blank line when done.",
            style="fg:cyan",
        )
        while True:
            line = (questionary.text(f"  {role.upper()} bias:").ask() or "").strip()
            if not line:
                break
            lines.append(line)
        if lines:
            biases[role] = lines
    return biases


def _ask_sprint() -> tuple[int, int, list[str]]:
    rounds = int(
        _required(
            questionary.text(
                "Debate rounds for sprint 1:",
                default="2",
                validate=lambda v: v.isdigit() and int(v) >= 1,
            ).ask()
        )
    )
    _picked = questionary.checkbox(
        "Sprint-1 participants:",
        choices=[questionary.Choice(s.upper(), value=s, checked=True) for s in SPOKES],
    ).ask()
    if _picked is None:
        raise SystemExit("setup cancelled")
    participants = _picked or list(SPOKES)
    quorum = int(
        _required(
            questionary.text(
                f"Approval quorum (1..{len(participants)}):",
                default=str(min(2, len(participants))),
                validate=lambda v, n=len(participants): v.isdigit() and 1 <= int(v) <= n,
            ).ask()
        )
    )
    return rounds, quorum, participants


def run_init_wizard() -> InitAnswers:
    """Drive the interactive flow. Call only when stdin is a TTY."""
    questionary.print("agensuite setup · Step 1/3 · Your idea", style="bold")
    idea = _ask_idea()

    questionary.print("Step 2/3 · Persona biases", style="bold")
    biases = _ask_biases()

    questionary.print("Step 3/3 · Sprint 1 config", style="bold")
    rounds, quorum, participants = _ask_sprint()

    questionary.print("Review", style="bold")
    questionary.print(f"  idea:         {idea}")
    if biases:
        for _role, _lines in biases.items():
            questionary.print(f"  {_role} bias:  {'; '.join(_lines)}")
    else:
        questionary.print("  biases:       (none)")
    questionary.print(f"  sprint-1:     rounds={rounds} quorum={quorum} participants={participants}")
    if not questionary.confirm("Create the project with these settings?", default=True).ask():
        raise SystemExit("setup cancelled")

    return InitAnswers(
        idea=idea,
        biases=biases,
        debate_rounds=rounds,
        approval_quorum=quorum,
        participants=participants,
    )
