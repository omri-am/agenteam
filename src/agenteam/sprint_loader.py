"""Parse ``sprints/*.md`` — YAML frontmatter + markdown body — into :class:`SprintConfig`.

Sprint files use the Jekyll-style frontmatter convention::

    ---
    id: sprint-1
    title: ...
    participants: [cpo, cto, cdo, cco]
    debate_rounds: 2
    approval_quorum: 2
    ---
    # Sprint 1: ...

    free-form markdown body...

The body is preserved verbatim and exposed on the parsed :class:`SprintConfig`
so the orchestrator can include it in spoke prompts.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import SprintConfig


_DELIM = "---"


class SprintParseError(ValueError):
    """Raised when a sprint file's frontmatter cannot be parsed."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"failed to parse sprint at {path}: {reason}")


def _split_frontmatter(text: str, path: Path) -> tuple[dict, str]:
    """Split ``text`` into ``(frontmatter_dict, body_markdown)``.

    Tolerates:
      * trailing whitespace on the delimiter line (``---   \\n``)
      * CRLF line endings from Windows-authored files
      * an entirely empty frontmatter block (``--- / ---``)

    Raises :class:`SprintParseError` for everything else so callers see
    a single exception type with file-path context.
    """
    # Normalize line endings up front so the parser doesn't have to think
    # about \r\n vs \n at every step.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    if not lines or lines[0].strip() != _DELIM:
        raise SprintParseError(path, "missing opening '---' frontmatter delimiter")

    closing_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _DELIM:
            closing_idx = i
            break
    if closing_idx is None:
        raise SprintParseError(path, "missing closing '---' frontmatter delimiter")

    fm_block = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :]).lstrip("\n")

    try:
        data = yaml.safe_load(fm_block) if fm_block.strip() else {}
    except yaml.YAMLError as e:
        raise SprintParseError(path, f"YAML frontmatter is invalid: {e}") from e

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SprintParseError(
            path,
            f"frontmatter must be a YAML mapping, got {type(data).__name__}",
        )

    return data, body


def parse_sprint(path: Path) -> SprintConfig:
    """Parse a single sprint file.

    The markdown body becomes the ``body`` field of :class:`SprintConfig`
    *unless* the frontmatter already supplies one — explicit frontmatter
    wins so authors can override the body if they want.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SprintParseError(path, f"could not read file: {e}") from e

    data, body = _split_frontmatter(text, path)
    data.setdefault("body", body)

    try:
        return SprintConfig(**data)
    except Exception as e:
        raise SprintParseError(path, f"frontmatter failed schema validation: {e}") from e


def list_sprints(root: Path) -> list[SprintConfig]:
    """Load every ``sprints/*.md`` under ``root`` in lexicographic order.

    Order matters: callers (notably the CLI's ``status``/``list`` commands)
    rely on sprint files appearing in filename order so a ``sprint-1.md`` /
    ``sprint-2.md`` naming convention sorts the way humans expect.
    """
    sprints_dir = root / "sprints"
    if not sprints_dir.exists():
        return []
    return [parse_sprint(p) for p in sorted(sprints_dir.glob("*.md"))]


def load_sprint(root: Path, sprint_id: str) -> SprintConfig:
    """Load a sprint by its declared ``id``.

    Lookup is by filename first (``sprints/<sprint_id>.md``) for the fast
    path, falling back to a full scan if the filename doesn't match the
    declared id. The fallback handles cases where authors rename a file
    without updating the frontmatter, and vice-versa.
    """
    direct = root / "sprints" / f"{sprint_id}.md"
    if direct.exists():
        cfg = parse_sprint(direct)
        if cfg.id == sprint_id:
            return cfg
        # File exists but its declared id disagrees; keep scanning rather
        # than silently returning the wrong sprint.

    for cfg in list_sprints(root):
        if cfg.id == sprint_id:
            return cfg

    raise FileNotFoundError(
        f"sprint not found: {sprint_id!r} "
        f"(looked at {direct} and scanned {root / 'sprints'})"
    )
