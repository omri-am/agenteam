"""Synchronous wrapper around the real ``git`` binary.

All git interactions for the *inner* simulated repository (the one under
``workspace/``) funnel through :class:`GitEngine`. There is a single
``_run_git`` chokepoint so that:

* every command is captured (stdout + stderr) and surfaces errors cleanly
  via :class:`GitCommandError`;
* per-author identity can be injected through ``-c user.name=…`` flags
  without mutating the worktree's persistent config;
* tests can monkeypatch one symbol to mock the entire git surface.

The outer host repository is untouched — only paths under ``root/workspace``
are read or written.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitCommandError(RuntimeError):
    """A git invocation returned a non-zero exit code.

    Wraps :class:`subprocess.CalledProcessError` so that ``stderr`` is part of
    the exception's ``str()`` form (the stdlib version truncates to just the
    return code, which makes diagnosis from log dumps painful).
    """

    def __init__(
        self,
        argv: list[str],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        rendered_cmd = " ".join(self.argv)
        details = (self.stderr.strip() or self.stdout.strip() or "<no output>")
        super().__init__(
            f"git command failed ({returncode}): {rendered_cmd}\n--- stderr ---\n{details}"
        )

    def as_called_process_error(self) -> subprocess.CalledProcessError:
        """Backwards-compat helper for callers that catch the stdlib type."""
        return subprocess.CalledProcessError(
            self.returncode, self.argv, self.stdout, self.stderr
        )


class MergeConflict(RuntimeError):
    """``git merge`` reported a conflict; the merge has been aborted."""

    def __init__(self, branch: str, details: str) -> None:
        super().__init__(f"merge conflict on {branch}: {details}")
        self.branch = branch
        self.details = details


@dataclass
class GitEngine:
    """Engine bound to an outer project ``root``.

    Layout under ``root``:

    * ``workspace/main/``         — the primary checkout of the inner repo
    * ``workspace/wt/<slug>/``    — one linked worktree per branch
    """

    root: Path

    # ---- path layout ----------------------------------------------------------

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def main_dir(self) -> Path:
        return self.workspace / "main"

    @property
    def wt_dir(self) -> Path:
        return self.workspace / "wt"

    @staticmethod
    def branch_slug(branch: str) -> str:
        """Filesystem-safe worktree directory name for ``branch``.

        Slashes (``feat/foo``) become double underscores so a single flat
        directory under ``workspace/wt/`` can host every branch without
        nesting collisions.
        """
        return branch.replace("/", "__")

    def worktree_path(self, branch: str) -> Path:
        if branch == "main":
            return self.main_dir
        return self.wt_dir / self.branch_slug(branch)

    # ---- low-level chokepoint -------------------------------------------------

    def _run_git(
        self,
        *args: str,
        cwd: Path,
        check: bool = True,
        extra_config: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run ``git <args>`` in ``cwd`` and return the completed process.

        ``extra_config`` is rendered as repeated ``-c key=value`` flags placed
        *before* the subcommand, which is how git overrides config for a
        single invocation. When ``check=True`` and the command fails, a
        :class:`GitCommandError` is raised carrying full stdout/stderr.
        """
        cmd: list[str] = ["git"]
        if extra_config:
            for k, v in extra_config.items():
                cmd += ["-c", f"{k}={v}"]
        cmd += list(args)
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            raise GitCommandError(
                argv=cmd,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        return completed

    # ---- bootstrap ------------------------------------------------------------

    def bootstrap(self, reset: bool = False) -> None:
        """Initialize ``workspace/main/`` and anchor it with a first commit.

        Fully idempotent: a second call is a no-op unless ``reset=True``, in
        which case the entire ``workspace/`` directory is wiped first.

        Sets local ``user.name`` / ``user.email`` immediately after ``git init``
        so the seeding commit works on hosts with no global git identity. GPG
        signing is also disabled at the repo level — the simulated repo is
        sandboxed plumbing and we never want commits to fail because the host
        has ``commit.gpgsign=true`` set globally.
        """
        if reset and self.workspace.exists():
            shutil.rmtree(self.workspace)

        self.main_dir.mkdir(parents=True, exist_ok=True)
        self.wt_dir.mkdir(parents=True, exist_ok=True)

        already_initialized = (self.main_dir / ".git").exists()
        head_ok = False
        if already_initialized:
            head_ok = (
                self._run_git(
                    "rev-parse", "--verify", "HEAD",
                    cwd=self.main_dir, check=False,
                ).returncode
                == 0
            )
            if head_ok and not reset:
                return

        if not already_initialized:
            self._run_git("init", "-b", "main", cwd=self.main_dir)

        # Repo-scoped identity & signing config so seeding commits always work.
        self._run_git("config", "user.name", "agensuite", cwd=self.main_dir)
        self._run_git("config", "user.email", "agensuite@local", cwd=self.main_dir)
        self._run_git("config", "commit.gpgsign", "false", cwd=self.main_dir)
        self._run_git("config", "tag.gpgsign", "false", cwd=self.main_dir)

        readme = self.main_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                "# agensuite workspace\n\n"
                "Simulated inner repository written to by spoke subagents.\n"
                "This file is the baseline commit so worktrees can branch off `main`.\n",
                encoding="utf-8",
            )

        gitignore = self.main_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("", encoding="utf-8")

        if not head_ok:
            self._run_git("add", "-A", cwd=self.main_dir)
            self._run_git("commit", "-m", "Initial workspace commit", cwd=self.main_dir)

    # ---- branches / worktrees -------------------------------------------------

    def checkout_branch(self, branch: str, base: str = "main") -> Path:
        """Materialize a linked worktree for ``branch`` and return its path.

        If a worktree for ``branch`` already exists, returns its path
        unchanged. On recoverable failures (``already exists`` /
        ``already checked out`` / ``already used by worktree``) the routine
        prunes stale worktree records, removes any orphan directory, and
        retries exactly once before raising.
        """
        slug = self.branch_slug(branch)
        path = self.wt_dir / slug
        if path.exists() and (path / ".git").exists():
            return path

        added = self._try_add_worktree(branch=branch, path=path, base=base)
        if added:
            return path

        # Recovery path: prune stale worktree records, nuke orphan dir, retry.
        self._run_git("worktree", "prune", cwd=self.main_dir, check=False)
        if path.exists():
            shutil.rmtree(path)
        added = self._try_add_worktree(
            branch=branch, path=path, base=base, raise_on_failure=True
        )
        return path

    def _try_add_worktree(
        self,
        *,
        branch: str,
        path: Path,
        base: str,
        raise_on_failure: bool = False,
    ) -> bool:
        """Single ``git worktree add`` attempt.

        Returns ``True`` on success. On failure, returns ``False`` if the
        failure matches a known recoverable signature; otherwise either
        raises :class:`GitCommandError` (if ``raise_on_failure=True``) or
        re-raises the recoverable signature for the caller to handle.
        """
        existing = self._run_git(
            "rev-parse", "--verify", branch, cwd=self.main_dir, check=False
        )
        if existing.returncode == 0:
            result = self._run_git(
                "worktree", "add", str(path), branch,
                cwd=self.main_dir, check=False,
            )
        else:
            result = self._run_git(
                "worktree", "add", "-b", branch, str(path), base,
                cwd=self.main_dir, check=False,
            )

        if result.returncode == 0:
            return True

        err = (result.stderr or "") + (result.stdout or "")
        recoverable = (
            "already exists" in err
            or "already used by worktree" in err
            or "already checked out" in err
        )
        if recoverable and not raise_on_failure:
            return False

        raise GitCommandError(
            argv=list(result.args),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    # ---- file IO inside a worktree -------------------------------------------

    def read_file(self, branch: str, rel_path: str) -> str:
        """Read ``rel_path`` inside the worktree for ``branch``."""
        wt = self.worktree_path(branch)
        return (wt / rel_path).read_text(encoding="utf-8")

    def write_file(self, branch: str, rel_path: str, content: str) -> Path:
        """Write ``content`` to ``rel_path`` inside the worktree for ``branch``.

        Parent directories are created on demand via ``Path.mkdir(parents=True,
        exist_ok=True)`` so spokes can write to deep paths without separate
        ``mkdir`` calls.
        """
        wt = self.worktree_path(branch)
        target = wt / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    # ---- commit / merge -------------------------------------------------------

    def commit(
        self,
        branch: str,
        author: str,
        message: str,
        files: list[str],
    ) -> str:
        """Stage ``files`` (or everything, if empty) and commit on ``branch``.

        ``author`` is injected via per-invocation ``-c user.name=…`` /
        ``-c user.email=…`` flags so the worktree's persistent config is
        never mutated — multiple spokes can share the same repo without
        racing on global identity. A ``nothing to commit`` outcome is *not*
        an error: the existing HEAD sha is returned so callers can treat the
        commit operation as effectively idempotent.
        """
        wt = self.worktree_path(branch)
        if files:
            self._run_git("add", "--", *files, cwd=wt)
        else:
            self._run_git("add", "-A", cwd=wt)

        author_cfg = {
            "user.name": author,
            "user.email": f"{author}@agensuite",
            "commit.gpgsign": "false",
        }
        result = self._run_git(
            "commit", "-m", message,
            cwd=wt, check=False, extra_config=author_cfg,
        )
        if result.returncode != 0:
            blob = (result.stderr or "") + (result.stdout or "")
            if "nothing to commit" in blob or "no changes added to commit" in blob:
                head = self._run_git("rev-parse", "--short", "HEAD", cwd=wt)
                return head.stdout.strip()
            raise GitCommandError(
                argv=list(result.args),
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )

        head = self._run_git("rev-parse", "--short", "HEAD", cwd=wt)
        return head.stdout.strip()

    def merge_branch(self, branch: str, base: str = "main") -> str:
        """Merge ``branch`` into ``base`` with ``--no-ff``.

        On conflict, the merge is aborted (so the worktree is left clean) and
        a :class:`MergeConflict` is raised carrying the conflict text. Returns
        the new HEAD short-sha on success.
        """
        base_wt = self.worktree_path(base)
        self._run_git("checkout", base, cwd=base_wt)

        result = self._run_git(
            "merge", "--no-ff", branch, "-m", f"Merge {branch}",
            cwd=base_wt, check=False,
            extra_config={"commit.gpgsign": "false"},
        )
        if result.returncode != 0:
            details = ((result.stderr or "") + (result.stdout or "")).strip()
            self._run_git("merge", "--abort", cwd=base_wt, check=False)
            raise MergeConflict(branch, details)

        head = self._run_git("rev-parse", "--short", "HEAD", cwd=base_wt)
        return head.stdout.strip()
