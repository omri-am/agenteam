"""Tests for the sprint frontmatter parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from agenteam.sprint_loader import (
    SprintParseError,
    list_sprints,
    load_sprint,
    parse_sprint,
)


@pytest.fixture()
def sprints_root(tmp_path: Path) -> Path:
    (tmp_path / "sprints").mkdir()
    return tmp_path


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


class TestParseSprint:
    def test_basic_frontmatter(self, sprints_root: Path) -> None:
        path = sprints_root / "sprints" / "s.md"
        _write(path,
               "---\nid: s\ntitle: T\nparticipants: [a]\n---\nbody-text\n")
        cfg = parse_sprint(path)
        assert cfg.id == "s"
        assert cfg.title == "T"
        assert cfg.body.strip() == "body-text"

    def test_crlf_line_endings(self, sprints_root: Path) -> None:
        path = sprints_root / "sprints" / "crlf.md"
        path.write_bytes(
            b"---\r\nid: x\r\ntitle: T\r\nparticipants: [a]\r\n---\r\nbody\r\n"
        )
        cfg = parse_sprint(path)
        assert cfg.id == "x"
        assert cfg.body.strip() == "body"

    def test_missing_opening_delimiter(self, sprints_root: Path) -> None:
        path = sprints_root / "sprints" / "no-fm.md"
        _write(path, "# just markdown")
        with pytest.raises(SprintParseError, match="opening"):
            parse_sprint(path)

    def test_missing_closing_delimiter(self, sprints_root: Path) -> None:
        path = sprints_root / "sprints" / "broken.md"
        _write(path, "---\nid: x\nno-close\n")
        with pytest.raises(SprintParseError, match="closing"):
            parse_sprint(path)

    def test_schema_validation_wrapped(self, sprints_root: Path) -> None:
        """SprintConfig validation failures surface as SprintParseError."""
        path = sprints_root / "sprints" / "bad.md"
        _write(path, "---\nid: x\ntitle: T\nparticipants: []\n---\nbody\n")
        with pytest.raises(SprintParseError, match="schema validation"):
            parse_sprint(path)


class TestListSprints:
    def test_lexicographic_order(self, sprints_root: Path) -> None:
        for sid in ["s-2", "s-1"]:
            _write(
                sprints_root / "sprints" / f"{sid}.md",
                f"---\nid: {sid}\ntitle: T\nparticipants: [a]\n---\n",
            )
        ids = [c.id for c in list_sprints(sprints_root)]
        assert ids == ["s-1", "s-2"]

    def test_missing_sprints_dir_returns_empty(self, tmp_path: Path) -> None:
        assert list_sprints(tmp_path) == []


class TestLoadSprint:
    def test_fast_path_by_filename(self, sprints_root: Path) -> None:
        _write(
            sprints_root / "sprints" / "s.md",
            "---\nid: s\ntitle: T\nparticipants: [a]\n---\n",
        )
        cfg = load_sprint(sprints_root, "s")
        assert cfg.id == "s"

    def test_full_scan_fallback_on_id_mismatch(self, sprints_root: Path) -> None:
        """File named ``misnamed.md`` whose frontmatter declares a different id."""
        _write(
            sprints_root / "sprints" / "misnamed.md",
            "---\nid: real-id\ntitle: T\nparticipants: [a]\n---\n",
        )
        cfg = load_sprint(sprints_root, "real-id")
        assert cfg.id == "real-id"

    def test_missing_raises_file_not_found(self, sprints_root: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_sprint(sprints_root, "nope")
