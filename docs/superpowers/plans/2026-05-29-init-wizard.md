# Guided `init` Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `agensuite init`'s "go edit the files yourself" step with a guided interactive CLI wizard that collects the startup idea, per-persona operational biases, and sprint-1 config, then writes them into the scaffolded files — no file opening, no long one-liners.

**Architecture:** A new `wizard.py` module isolates all `questionary` prompt I/O and returns an `InitAnswers` pydantic model. `init` validates the target dir, obtains answers (interactive wizard only when `--idea` is absent AND stdin is a TTY; otherwise idea-from-flag/stdin + defaults), scaffolds the templates, then applies answers through pure, unit-tested functions (`_substitute_tokens`, `_append_operational_bias`, new `_set_sprint_frontmatter`).

**Tech Stack:** Python, Typer (existing CLI), Pydantic v2 (existing), PyYAML (existing), `questionary` (new dependency, engine `prompt_toolkit` already present), pytest (subprocess e2e + new direct unit tests).

---

## File Structure

- `src/agensuite/cli.py` — modify `init` (lines ~271-327) to validate-dir-then-obtain-answers-then-scaffold-then-apply; add pure helper `_set_sprint_frontmatter`; add non-interactive answer builder. Reuses existing `_substitute_tokens` (1466), `_append_operational_bias` (1478), `CHIEF_ROLES` (108).
- `src/agensuite/wizard.py` — **new.** `InitAnswers` model + `run_init_wizard()`. Only file that imports `questionary`.
- `pyproject.toml` — add `questionary` to `[project] dependencies`.
- `tests/test_wizard_units.py` — **new.** Direct unit tests for `_set_sprint_frontmatter` and `InitAnswers`.
- `tests/test_cli.py` — modify: existing `TestInit` tests stay green; add a non-TTY defaults smoke assertion.

---

## Task 1: Add `_set_sprint_frontmatter` pure function (TDD)

**Files:**
- Modify: `src/agensuite/cli.py` (add function near `_substitute_tokens`, ~line 1476)
- Test: `tests/test_wizard_units.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wizard_units.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wizard_units.py -v`
Expected: FAIL with `ImportError: cannot import name '_set_sprint_frontmatter'`

- [ ] **Step 3: Add the implementation**

In `src/agensuite/cli.py`, add `import yaml` to the import block (after `from pathlib import Path`, around line 24 — check it is not already imported) and insert this function immediately after `_substitute_tokens` (ends ~line 1475):

```python
def _set_sprint_frontmatter(
    content: str,
    *,
    rounds: int,
    quorum: int,
    participants: list[str],
) -> str:
    """Rewrite the YAML frontmatter of a sprint file, preserving its body.

    Updates ``debate_rounds`` / ``approval_quorum`` / ``participants`` and
    leaves every other frontmatter key and the markdown body untouched.
    Validates ``rounds >= 1`` and ``1 <= quorum <= len(participants)``.
    """
    if rounds < 1:
        raise ValueError("rounds must be >= 1")
    if not (1 <= quorum <= len(participants)):
        raise ValueError(
            "quorum must satisfy 1 <= quorum <= number of participants"
        )

    if not content.startswith("---\n"):
        raise ValueError("sprint file has no YAML frontmatter")
    _, front, body = content.split("---\n", 2)

    meta = yaml.safe_load(front) or {}
    meta["debate_rounds"] = rounds
    meta["approval_quorum"] = quorum
    meta["participants"] = participants

    new_front = yaml.safe_dump(meta, sort_keys=False, default_flow_style=None)
    return f"---\n{new_front}---\n{body}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wizard_units.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/agensuite/cli.py tests/test_wizard_units.py
git commit -m "feat: add _set_sprint_frontmatter pure helper for init wizard"
```

---

## Task 2: Add `InitAnswers` model + non-interactive defaults builder (TDD)

**Files:**
- Create: `src/agensuite/wizard.py`
- Test: `tests/test_wizard_units.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wizard_units.py`:

```python
from agensuite.wizard import InitAnswers, default_answers


def test_init_answers_defaults() -> None:
    a = InitAnswers(idea="A trading app")
    assert a.idea == "A trading app"
    assert a.biases == {}
    assert a.debate_rounds == 2
    assert a.approval_quorum == 2
    assert a.participants == ["cpo", "cto", "cdo", "cco"]


def test_default_answers_uses_idea_and_defaults() -> None:
    a = default_answers("My idea")
    assert a.idea == "My idea"
    assert a.biases == {}
    assert a.debate_rounds == 2
    assert a.participants == ["cpo", "cto", "cdo", "cco"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_wizard_units.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agensuite.wizard'`

- [ ] **Step 3: Create the module**

Create `src/agensuite/wizard.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_wizard_units.py -v`
Expected: PASS (6 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/agensuite/wizard.py tests/test_wizard_units.py
git commit -m "feat: add InitAnswers model and default_answers builder"
```

---

## Task 3: Implement `run_init_wizard` (questionary prompt layer)

**Files:**
- Modify: `src/agensuite/wizard.py`
- Modify: `pyproject.toml`

No unit test drives the live prompts (a non-TTY subprocess cannot fake the
TTY questionary needs). This task wires the UI; behavior is covered by the
pure helpers (Tasks 1-2) and the orchestration smoke test (Task 4).

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, change the `dependencies` block to include `questionary`:

```toml
dependencies = [
    "pydantic>=2",
    "typer>=0.12",
    "pyyaml>=6",
    "questionary>=2",
]
```

- [ ] **Step 2: Install it into the working environment**

Run: `python -m pip install -e .`
Expected: completes; `python -c "import questionary"` exits 0.

- [ ] **Step 3: Add `run_init_wizard` to `wizard.py`**

Append to `src/agensuite/wizard.py`:

```python
import questionary


def _ask_idea() -> str:
    while True:
        idea = (questionary.text("Describe your startup in one line:").ask() or "").strip()
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
        questionary.text(
            "Debate rounds for sprint 1:",
            default="2",
            validate=lambda v: v.isdigit() and int(v) >= 1,
        ).ask()
    )
    participants = questionary.checkbox(
        "Sprint-1 participants:",
        choices=[questionary.Choice(s.upper(), value=s, checked=True) for s in SPOKES],
    ).ask() or list(SPOKES)
    quorum = int(
        questionary.text(
            f"Approval quorum (1..{len(participants)}):",
            default="2",
            validate=lambda v, n=len(participants): v.isdigit() and 1 <= int(v) <= n,
        ).ask()
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

    return InitAnswers(
        idea=idea,
        biases=biases,
        debate_rounds=rounds,
        approval_quorum=quorum,
        participants=participants,
    )
```

- [ ] **Step 4: Smoke-check import**

Run: `python -c "from agensuite.wizard import run_init_wizard; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**

```bash
git add src/agensuite/wizard.py pyproject.toml
git commit -m "feat: add questionary-driven run_init_wizard"
```

---

## Task 4: Wire the wizard into `init` (TDD via existing + new e2e tests)

**Files:**
- Modify: `src/agensuite/cli.py` (the `init` command, lines ~271-327)
- Test: `tests/test_cli.py` (`TestInit`)

- [ ] **Step 1: Add a failing test for the non-TTY defaults path**

In `tests/test_cli.py`, inside `class TestInit`, add:

```python
    def test_idea_flag_applies_sprint_defaults(self, tmp_path: Path) -> None:
        """--idea (non-TTY) writes the default sprint config, no prompts."""
        env = _agensuite_env()
        p = _run(
            ["init", "defaults-app", "--idea", "A budgeting tool"],
            cwd=tmp_path,
            env=env,
        )
        assert p.returncode == 0, p.stderr
        sprint = (tmp_path / "defaults-app" / "sprints" / "sprint-1.md").read_text()
        import yaml as _yaml

        meta = _yaml.safe_load(sprint.split("---\n", 2)[1])
        assert meta["debate_rounds"] == 2
        assert meta["approval_quorum"] == 2
        assert meta["participants"] == ["cpo", "cto", "cdo", "cco"]
        # The misleading "customize files" line is gone.
        assert "customize AGENTS.md" not in p.stdout
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_cli.py::TestInit::test_idea_flag_applies_sprint_defaults -v`
Expected: FAIL — `"customize AGENTS.md"` is still printed by the current `init`.

- [ ] **Step 3: Rewrite the `init` body**

In `src/agensuite/cli.py`, replace the body of `init` (from the `if idea is None:` block at ~line 287 through the final `typer.echo(...)` at ~line 327) with:

```python
    target = _resolve_target_dir(target_dir)
    if target.exists() and not target.is_dir():
        raise _err(f"target path exists and is not a directory: {target}")
    if target.exists() and any(target.iterdir()):
        raise _err(f"target directory is not empty: {target}")

    templates = _resource_templates_root()
    if not templates.is_dir():
        raise _err("packaged templates are missing; reinstall agensuite")

    # Resolve answers. The interactive wizard runs ONLY when no --idea flag
    # was given AND stdin is a real TTY. Otherwise we stay non-interactive:
    # idea comes from the flag or a piped stdin line, everything else defaults.
    from .wizard import default_answers, run_init_wizard

    if idea is None and sys.stdin.isatty():
        answers = run_init_wizard()
    else:
        if idea is None:
            idea = typer.prompt("Describe your startup idea")
        idea = idea.strip()
        if not idea:
            raise _err("idea must be a non-empty string")
        answers = default_answers(idea)

    try:
        target.mkdir(parents=True, exist_ok=True)
        _copy_resource_tree(
            templates.joinpath("AGENTS.md"), target / "AGENTS.md", idea=answers.idea
        )
        _copy_resource_tree(
            templates.joinpath(".claude", "agents"),
            target / ".claude" / "agents",
            idea=answers.idea,
        )
        _copy_resource_tree(
            templates.joinpath("sprints", "sprint-1.md"),
            target / "sprints" / "sprint-1.md",
            idea=answers.idea,
        )

        # Apply per-persona biases.
        for role, lines in answers.biases.items():
            agent_file = target / ".claude" / "agents" / f"{role}.md"
            text = agent_file.read_text(encoding="utf-8")
            for line in lines:
                text = _append_operational_bias(text, line)
            agent_file.write_text(text, encoding="utf-8")

        # Apply sprint-1 config.
        sprint_file = target / "sprints" / "sprint-1.md"
        sprint_file.write_text(
            _set_sprint_frontmatter(
                sprint_file.read_text(encoding="utf-8"),
                rounds=answers.debate_rounds,
                quorum=answers.approval_quorum,
                participants=answers.participants,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        raise _err(f"init failed: {e}") from e

    typer.echo(f"Successfully initialized agensuite project at {target}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(f"  cd {target}")
    typer.echo("  agensuite bootstrap")
    typer.echo("  open this folder inside your coding agent session")
```

- [ ] **Step 4: Add the `sys` import**

In `src/agensuite/cli.py`, confirm `import sys` is present in the import block (after `from importlib.abc import Traversable`, ~line 24). If missing, add it.

- [ ] **Step 5: Run the full init test suite**

Run: `python -m pytest tests/test_cli.py::TestInit tests/test_wizard_units.py -v`
Expected: PASS — including the preserved `test_idea_flag_substitutes_tokens`, `test_interactive_prompt_fallback` (piped stdin, non-TTY → reads idea line), `test_init_refuses_to_overwrite_non_empty_dir`, `test_init_accepts_absolute_path`, and the new `test_idea_flag_applies_sprint_defaults`.

- [ ] **Step 6: Commit**

```bash
git add src/agensuite/cli.py tests/test_cli.py
git commit -m "feat: drive init through the guided wizard; drop edit-files prompt"
```

---

## Task 5: Full suite + manual interactive sanity check

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Manual interactive smoke (real TTY)**

Run in a real terminal (not captured):
```bash
cd /tmp && rm -rf wizard-demo && agensuite init wizard-demo
```
Expected: arrow-key / text prompts for idea → per-persona biases → sprint config → success message with NO "customize AGENTS.md" line. Then verify:
```bash
grep -c "customize AGENTS.md" /dev/stdin <<<"$(agensuite init /tmp/x2 --idea probe)"   # 0
cat /tmp/wizard-demo/sprints/sprint-1.md   # frontmatter reflects answers
```

- [ ] **Step 3: Commit (only if any fix was needed)**

```bash
git add -A && git commit -m "test: verify init wizard end-to-end"
```

---

## Self-Review Notes

- **Spec coverage:** idea (Task 4 scaffold), per-persona multi-line biases (Task 3 `_ask_biases` + Task 4 apply loop), sprint config (Task 1 `_set_sprint_frontmatter` + Task 3 `_ask_sprint` + Task 4 apply), questionary lib + dep (Task 3), non-TTY/`--idea` bypass (Task 4 guard), removal of "customize files" line (Task 4 + asserted in test), pure-fn testing (Tasks 1-2). All spec sections mapped.
- **Type consistency:** `InitAnswers(idea, biases: dict[str,list[str]], debate_rounds, approval_quorum, participants)` defined in Task 2 and consumed unchanged in Task 4. `_set_sprint_frontmatter(content, *, rounds, quorum, participants)` defined Task 1, called identically Task 4. `default_answers(idea)`/`run_init_wizard()` signatures consistent across Tasks 2-4.
- **Out of scope (per spec):** no new metadata tokens, no TUI, `chief customize` untouched.
