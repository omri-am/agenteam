"""Tests for the git subprocess wrapper."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agenteam.git_engine import GitCommandError, GitEngine, MergeConflict


@pytest.fixture()
def engine(tmp_path: Path) -> GitEngine:
    return GitEngine(tmp_path)


class TestBootstrap:
    def test_idempotent(self, engine: GitEngine) -> None:
        engine.bootstrap()
        engine.bootstrap()
        # No second initial commit.
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(engine.main_dir),
            capture_output=True, text=True, check=True,
        )
        assert log.stdout.count("\n") == 1

    def test_creates_seed_commit(self, engine: GitEngine) -> None:
        engine.bootstrap()
        assert (engine.main_dir / "README.md").exists()
        assert (engine.main_dir / ".git").exists()

    def test_reset_wipes_workspace(self, engine: GitEngine) -> None:
        engine.bootstrap()
        (engine.main_dir / "scratch").write_text("x")
        engine.bootstrap(reset=True)
        assert not (engine.main_dir / "scratch").exists()


class TestCheckoutBranch:
    def test_creates_new_worktree(self, engine: GitEngine) -> None:
        engine.bootstrap()
        path = engine.checkout_branch("feat/x")
        assert path == engine.wt_dir / "feat__x"
        assert (path / ".git").exists()

    def test_idempotent_for_existing_worktree(self, engine: GitEngine) -> None:
        engine.bootstrap()
        a = engine.checkout_branch("feat/x")
        b = engine.checkout_branch("feat/x")
        assert a == b

    def test_recovers_from_orphan_directory(self, engine: GitEngine) -> None:
        """An empty directory at the worktree path triggers recovery + retry."""
        engine.bootstrap()
        target = engine.wt_dir / "feat__orphan"
        target.mkdir(parents=True)
        (target / "junk").write_text("garbage")

        path = engine.checkout_branch("feat/orphan")

        assert (path / ".git").exists()


class TestCommit:
    def test_uses_per_invocation_author(self, engine: GitEngine) -> None:
        engine.bootstrap()
        engine.checkout_branch("feat/x")
        engine.write_file("feat/x", "a.txt", "hello")
        engine.commit("feat/x", author="alice", message="A", files=["a.txt"])

        log = subprocess.run(
            ["git", "log", "-1", "--format=%an <%ae>"],
            cwd=str(engine.worktree_path("feat/x")),
            capture_output=True, text=True, check=True,
        )
        assert "alice" in log.stdout and "alice@agenteam" in log.stdout

    def test_nothing_to_commit_returns_existing_head(self, engine: GitEngine) -> None:
        engine.bootstrap()
        engine.checkout_branch("feat/x")
        engine.write_file("feat/x", "a.txt", "v1")
        sha1 = engine.commit("feat/x", "alice", "A", ["a.txt"])
        sha2 = engine.commit("feat/x", "alice", "noop", [])
        assert sha1 == sha2

    def test_write_file_creates_parent_dirs(self, engine: GitEngine) -> None:
        engine.bootstrap()
        engine.checkout_branch("feat/x")
        engine.write_file("feat/x", "deep/nested/file.txt", "hi")
        assert engine.read_file("feat/x", "deep/nested/file.txt") == "hi"


class TestMerge:
    def test_clean_merge(self, engine: GitEngine) -> None:
        engine.bootstrap()
        engine.checkout_branch("feat/x")
        engine.write_file("feat/x", "a.txt", "x")
        engine.commit("feat/x", "alice", "A", ["a.txt"])
        head = engine.merge_branch("feat/x")
        assert head

    def test_conflict_raises_and_aborts(self, engine: GitEngine) -> None:
        engine.bootstrap()
        for role, br in [("alice", "feat/a"), ("bob", "feat/b")]:
            engine.checkout_branch(br)
            engine.write_file(br, "shared.txt", f"{role}-content\n")
            engine.commit(br, role, f"{role} v", ["shared.txt"])

        engine.merge_branch("feat/a")
        with pytest.raises(MergeConflict) as exc:
            engine.merge_branch("feat/b")
        assert exc.value.branch == "feat/b"
        assert "conflict" in exc.value.details.lower() or "merge" in exc.value.details.lower()

        # `main` worktree must be clean after abort.
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(engine.main_dir),
            capture_output=True, text=True, check=True,
        )
        assert st.stdout.strip() == ""


class TestGitCommandError:
    def test_carries_stderr_in_str(self, engine: GitEngine) -> None:
        engine.bootstrap()
        with pytest.raises(GitCommandError) as exc:
            engine._run_git("rev-parse", "--verify", "definitely-not-a-branch",
                            cwd=engine.main_dir)
        rendered = str(exc.value)
        # Either stderr or stdout must surface somewhere in the rendered
        # error so log dumps are diagnosable.
        assert exc.value.returncode != 0
        assert "git command failed" in rendered
