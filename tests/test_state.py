"""Tests for atomic writes, the directory lock, and the JSON registries."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from agenteam.models import DebateState, PullRequest
from agenteam.state import (
    DebateStore,
    PRRegistry,
    StateLockTimeout,
    _atomic_write,
    clear_stale_lock,
    ensure_dirs,
    state_lock,
)


class TestEnsureDirs:
    def test_idempotent(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        ensure_dirs(tmp_path)
        assert (tmp_path / "state" / "debates").is_dir()


class TestAtomicWrite:
    def test_writes_content(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "f.json"
        _atomic_write(target, '{"a": 1}\n')
        assert target.read_text() == '{"a": 1}\n'

    def test_no_orphan_temp_after_success(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        _atomic_write(target, "x")
        survivors = list(tmp_path.iterdir())
        assert survivors == [target]

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "f.json"
        _atomic_write(target, "v1")
        _atomic_write(target, "v2")
        assert target.read_text() == "v2"


class TestStateLock:
    def test_acquire_and_release(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        with state_lock(tmp_path, timeout_s=1.0):
            assert (tmp_path / "state" / ".lock").is_dir()
        assert not (tmp_path / "state" / ".lock").exists()

    def test_timeout_when_held(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        holding = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with state_lock(tmp_path, timeout_s=5.0):
                holding.set()
                release.wait(timeout=3.0)

        worker = threading.Thread(target=hold)
        worker.start()
        try:
            assert holding.wait(timeout=3.0)
            with pytest.raises(StateLockTimeout) as exc:
                with state_lock(tmp_path, timeout_s=0.3, poll_interval_s=0.05):
                    pytest.fail("should not have acquired")
            assert exc.value.owner  # owner metadata recorded
        finally:
            release.set()
            worker.join()

    def test_clear_stale_lock(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        lock_dir = tmp_path / "state" / ".lock"
        lock_dir.mkdir()
        (lock_dir / "owner").write_text("zombie")

        assert clear_stale_lock(tmp_path) is True
        assert clear_stale_lock(tmp_path) is False
        assert not lock_dir.exists()


class TestPRRegistry:
    def _pr(self, pid: str = "pr-1") -> PullRequest:
        return PullRequest(
            id=pid, title="T", branch="b", author="a", sprint_id="s",
        )

    def test_round_trip(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        pr = self._pr()
        PRRegistry.save(tmp_path, {pr.id: pr})
        loaded = PRRegistry.load(tmp_path)
        assert loaded[pr.id].title == "T"

    def test_empty_when_no_file(self, tmp_path: Path) -> None:
        assert PRRegistry.load(tmp_path) == {}

    def test_corrupt_json_raises_with_path(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        (tmp_path / "state" / "prs.json").write_text("{ bogus")
        with pytest.raises(ValueError, match="corrupt"):
            PRRegistry.load(tmp_path)


class TestDebateStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        ds = DebateState(sprint_id="s")
        DebateStore.save(tmp_path, ds)
        loaded = DebateStore.load(tmp_path, "s")
        assert loaded is not None
        assert loaded.sprint_id == "s"

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert DebateStore.load(tmp_path, "missing") is None

    def test_list_sprint_ids(self, tmp_path: Path) -> None:
        ensure_dirs(tmp_path)
        for sid in ("s1", "s2"):
            DebateStore.save(tmp_path, DebateState(sprint_id=sid))
        assert DebateStore.list_sprint_ids(tmp_path) == ["s1", "s2"]
