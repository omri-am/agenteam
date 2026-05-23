"""End-to-end CLI tests driven via subprocess.

These tests are the closest match to how a coding-agent orchestrator
actually drives the CLI. Each test spawns ``python -m agenteam.cli`` with
``AGENTEAM_ROOT`` pointing at an isolated tmp directory.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class TestBootstrap:
    def test_idempotent(self, cli) -> None:
        cli("bootstrap")
        cli("bootstrap")  # second call must not error

    def test_banner_emitted_on_stderr(self, cli) -> None:
        """Banner must appear on stderr (not stdout) so orchestrators that
        parse `bootstrap` stdout get a clean machine-readable channel."""
        p = cli("bootstrap")
        assert "Agent-native C-suite orchestration" in p.stderr
        assert "Agent-native C-suite orchestration" not in p.stdout
        # Workspace-ready info lines stay on stdout.
        assert "workspace ready at" in p.stdout

    def test_bare_invocation_prints_banner_and_help(self, cli) -> None:
        """`agenteam` with no subcommand: banner on stderr, help on stdout,
        exit code 0 (user explicitly asked for help by typing the bare cmd)."""
        p = cli(expect_ok=True)
        assert p.returncode == 0
        assert "Agent-native C-suite orchestration" in p.stderr
        assert "Usage:" in p.stdout
        assert "bootstrap" in p.stdout  # subcommand listing renders


class TestSprintCommands:
    def test_show_emits_valid_json(self, cli) -> None:
        out = cli("sprint", "show", "s").stdout
        assert json.loads(out)["id"] == "s"

    def test_show_missing_sprint_exits_1(self, cli) -> None:
        p = cli("sprint", "show", "nope", expect_ok=False)
        assert p.returncode == 1
        assert "Traceback" not in p.stderr
        assert "During handling" not in p.stderr

    def test_list_includes_sprint(self, cli) -> None:
        out = cli("sprint", "list").stdout
        assert "s\t" in out


class TestEndToEnd:
    def _open_pr(self, cli, project_root: Path, role: str, br: str, path: str) -> str:
        cli("branch", "create", br)
        slug = br.replace("/", "__")
        f = project_root / "workspace" / "wt" / slug / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"draft by {role}\n")
        cli("commit", "--branch", br, "--author", role,
            "--message", f"{role} draft", "--files", path)
        return cli("pr", "open", "--branch", br, "--author", role,
                   "--title", f"{role}: t", "--sprint", "s",
                   "--files", path).stdout.strip()

    def test_full_sprint_loop(self, cli, project_root: Path) -> None:
        cli("bootstrap")
        pr_a = self._open_pr(cli, project_root, "a", "feat/a/x", "fa.md")
        pr_b = self._open_pr(cli, project_root, "b", "feat/b/x", "fb.md")

        # Approve each PR by one non-author (quorum=1).
        cli("pr", "comment", "--id", pr_a, "--reviewer", "b",
            "--comment", "lgtm", "--approve")
        cli("pr", "comment", "--id", pr_b, "--reviewer", "a",
            "--comment", "lgtm", "--approve")

        # Drain debate schedule. 2 PRs * 2 non-author reviewers * 2 rounds = 8.
        consumed = 0
        while True:
            out = json.loads(cli("debate", "next-turn", "--sprint", "s").stdout)
            if out.get("done"):
                break
            consumed += 1
            assert consumed < 20
        assert consumed == 8

        cli("pr", "merge", "--id", pr_a)
        cli("pr", "merge", "--id", pr_b)

        adr = json.loads(cli("adr", "record", "--sprint", "s").stdout)
        assert adr["adr_id"].startswith("adr-")
        adr_file = next((project_root / "workspace" / "main" / "governance").glob("*.md"))
        body = adr_file.read_text()
        assert "Status:** Accepted" in body
        assert "Merged:" in body

    def test_self_review_rejected(self, cli, project_root: Path) -> None:
        cli("bootstrap")
        pr_a = self._open_pr(cli, project_root, "a", "feat/a/x", "fa.md")
        p = cli("pr", "comment", "--id", pr_a, "--reviewer", "a",
                "--comment", "self", expect_ok=False)
        assert p.returncode == 1
        assert "cannot review own PR" in p.stderr

    def test_non_participant_rejected(self, cli, project_root: Path) -> None:
        cli("bootstrap")
        cli("branch", "create", "feat/intruder/x")
        p = cli("pr", "open", "--branch", "feat/intruder/x",
                "--author", "intruder", "--title", "X", "--sprint", "s",
                expect_ok=False)
        assert p.returncode == 1
        assert "not a participant" in p.stderr

    def test_merge_conflict_exits_2(self, cli, project_root: Path) -> None:
        cli("bootstrap")
        # Both touch the same file.
        for role, br in [("a", "feat/a/x"), ("b", "feat/b/x")]:
            cli("branch", "create", br)
            slug = br.replace("/", "__")
            (project_root / "workspace" / "wt" / slug / "shared.md").write_text(
                f"value={role}\n"
            )
            cli("commit", "--branch", br, "--author", role,
                "--message", f"{role}", "--files", "shared.md")
            other = "b" if role == "a" else "a"
            pid = cli("pr", "open", "--branch", br, "--author", role,
                      "--title", f"{role}", "--sprint", "s",
                      "--files", "shared.md").stdout.strip()
            cli("pr", "comment", "--id", pid, "--reviewer", other,
                "--comment", "k", "--approve")
            if role == "a":
                pr_a = pid
            else:
                pr_b = pid

        cli("pr", "merge", "--id", pr_a)
        p = cli("pr", "merge", "--id", pr_b, expect_ok=False)
        assert p.returncode == 2  # conflict-specific code
        assert "merge conflict" in p.stderr
        # `main` must be clean after auto-abort.
        main_dir = project_root / "workspace" / "main"
        import subprocess
        st = subprocess.run(["git", "status", "--porcelain"],
                            cwd=str(main_dir), capture_output=True, text=True)
        assert st.stdout.strip() == ""


class TestMidDebatePR:
    """Regression test for review finding #1.

    Opening a PR after the debate cursor has already advanced must NOT
    rebuild the entire schedule (that would change which logical turn
    ``cursor`` indexes into). Instead it must append the new PR's review
    slots at the tail, leaving the in-progress prefix untouched.
    """

    def _open_pr(self, cli, project_root: Path, role: str, br: str, path: str) -> str:
        cli("branch", "create", br)
        slug = br.replace("/", "__")
        f = project_root / "workspace" / "wt" / slug / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"draft by {role}\n")
        cli("commit", "--branch", br, "--author", role,
            "--message", f"{role} draft", "--files", path)
        return cli("pr", "open", "--branch", br, "--author", role,
                   "--title", f"{role}: t", "--sprint", "s",
                   "--files", path).stdout.strip()

    def test_append_only_when_cursor_advanced(self, cli, project_root: Path) -> None:
        cli("bootstrap")
        pr_a = self._open_pr(cli, project_root, "a", "feat/a/x", "fa.md")
        pr_b = self._open_pr(cli, project_root, "b", "feat/b/x", "fb.md")

        debate_path = project_root / "state" / "debates" / "s.json"
        before = json.loads(debate_path.read_text())
        initial_len = len(before["schedule"])
        assert initial_len == 8  # 2 PRs * 2 reviewers * 2 rounds

        # Consume two turns
        for _ in range(2):
            json.loads(cli("debate", "next-turn", "--sprint", "s").stdout)
        mid = json.loads(debate_path.read_text())
        assert mid["cursor"] == 2
        prefix = mid["schedule"][:2]

        # Open PR-C mid-debate
        pr_c = self._open_pr(cli, project_root, "c", "feat/c/x", "fc.md")
        after = json.loads(debate_path.read_text())

        # Invariants:
        assert after["cursor"] == 2, "cursor must be preserved"
        assert after["schedule"][:2] == prefix, "consumed slots unchanged"
        # Original 8 turns intact, 4 new (PR-C: 2 reviewers * 2 rounds) appended
        assert len(after["schedule"]) == initial_len + 4
        tail_targets = {t["target_pr_id"] for t in after["schedule"][initial_len:]}
        assert tail_targets == {pr_c}
