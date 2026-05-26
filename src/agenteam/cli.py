"""agenteam CLI — the only mutation surface for orchestrating coding agents.

Every state-changing operation flows through one of these subcommands so that
any coding-agent platform (Claude Code, Codex, Cursor, ...) can drive the
same workflow identically by shelling out to ``agenteam ...``.

Exit-code convention:

* ``0`` — success (also: bare ``agenteam`` invocation that prints banner +
  help, since "user asked for help and got it" is not a failure)
* ``1`` — generic / user error (unknown PR, sprint not found, schema
  validation failure, missing quorum, ...)
* ``2`` — merge conflict (so orchestrators can branch on ``$?`` and treat
  conflicts as recoverable rather than fatal)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path
from typing import Optional

import typer

from .git_engine import GitCommandError, GitEngine, MergeConflict
from .models import (
    DebateState,
    DecisionRecord,
    Message,
    MessageType,
    PRStatus,
    PullRequest,
    ReviewComment,
    SprintConfig,
)
from .sprint_loader import SprintParseError, list_sprints, load_sprint
from .state import (
    DebateStore,
    PRRegistry,
    StateLockTimeout,
    clear_stale_lock,
    ensure_dirs,
    state_lock,
)


# ---------------------------------------------------------------------------
# Typer app graph
# ---------------------------------------------------------------------------


_BANNER = """\
 █████╗  ██████╗ ███████╗███╗   ██╗████████╗███████╗ █████╗ ███╗   ███╗
██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██╔════╝██╔══██╗████╗ ████║
███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   █████╗  ███████║██╔████╔██║
██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ██╔══╝  ██╔══██║██║╚██╔╝██║
██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ███████╗██║  ██║██║ ╚═╝ ██║
╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝
              Agent-native C-suite orchestration
"""


def _print_banner() -> None:
    """Emit the banner on stderr so stdout stays the machine-parseable channel.

    Orchestrators that pipe ``agenteam bootstrap`` output (or any future
    subcommand) get a clean stdout; humans still see the banner because
    interactive shells render stderr alongside stdout.
    """
    typer.echo(_BANNER, err=True)


app = typer.Typer(
    add_completion=False,
    help="Agent-native C-suite orchestration plumbing.",
)
pr_app = typer.Typer(help="Pull-request operations.", no_args_is_help=True)
sprint_app = typer.Typer(help="Inspect sprint definitions.", no_args_is_help=True)
debate_app = typer.Typer(help="Drive the turn-based debate.", no_args_is_help=True)
adr_app = typer.Typer(help="Decision-record operations.", no_args_is_help=True)
state_app = typer.Typer(help="State-store maintenance.", no_args_is_help=True)

app.add_typer(pr_app, name="pr")
app.add_typer(sprint_app, name="sprint")
app.add_typer(debate_app, name="debate")
app.add_typer(adr_app, name="adr")
app.add_typer(state_app, name="state")


# ---------------------------------------------------------------------------
# Globals / helpers
# ---------------------------------------------------------------------------


@app.callback(invoke_without_command=True)
def _global(
    ctx: typer.Context,
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        envvar="AGENTEAM_ROOT",
        help="Project root containing sprints/, workspace/, state/. "
             "Defaults to the current working directory.",
    ),
) -> None:
    """Set the project root for every subcommand.

    Threads through ``ctx.obj`` so subcommands don't each have to parse the
    same flag. The env var fallback (``AGENTEAM_ROOT``) is what makes the
    CLI usable from inside spawned subagents that don't share the
    orchestrator's CWD.
    """
    resolved = (root or Path.cwd()).resolve()
    ctx.obj = {"root": resolved}
    if ctx.invoked_subcommand is None:
        _print_banner()
        typer.echo(ctx.get_help())
        raise typer.Exit()


def _root(ctx: typer.Context) -> Path:
    return ctx.obj["root"] if ctx.obj else Path.cwd()


def _engine(ctx: typer.Context) -> GitEngine:
    return GitEngine(_root(ctx))


def _resource_templates_root() -> Traversable:
    """Return the immutable templates bundled with the installed package."""
    return resources.files("agenteam").joinpath("templates")


def _copy_resource_tree(
    source: Traversable,
    destination: Path,
) -> None:
    """Copy an importlib.resources tree to a real filesystem path.

    ``Traversable`` resources may come from an editable source tree, a normal
    site-packages directory, or a zip-style importer. Reading bytes through the
    resource API keeps ``init`` independent of where Python installed us.
    """
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir(), key=lambda p: p.name):
            _copy_resource_tree(child, destination / child.name)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def _resolve_target_dir(target_dir: Path) -> Path:
    """Resolve a user-supplied init target relative to the active shell CWD."""
    target = target_dir.expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    return target.resolve(strict=False)


def _err(msg: str, code: int = 1) -> typer.Exit:
    """Print to stderr and return a ``typer.Exit`` for ``raise _err(...)``.

    When raising inside an ``except`` block, prefer ``raise _err(...) from e``
    (or ``from None`` when suppressing the chain entirely) to keep
    ``__context__`` clean and avoid the "During handling of the above
    exception, another exception occurred" framing.
    """
    typer.echo(msg, err=True)
    return typer.Exit(code=code)


def _short_id(*parts: str) -> str:
    """Deterministic 6-hex-char id derived from ``parts``.

    SHA-1 is fine here — these ids never authenticate anything, they just
    have to be collision-resistant enough across a single project.
    """
    h = hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()
    return h[:6]


def _load_sprint_or_die(root: Path, sprint_id: str) -> SprintConfig:
    """Load a sprint, mapping every failure mode to a clean CLI error."""
    try:
        return load_sprint(root, sprint_id)
    except FileNotFoundError as e:
        raise _err(str(e)) from e
    except SprintParseError as e:
        raise _err(f"invalid sprint file: {e}") from e


def _rebuild_schedule(
    debate: DebateState,
    cfg: SprintConfig,
    sprint_prs: list[PullRequest],
) -> None:
    """Refresh ``debate.schedule`` to reflect the current set of sprint PRs.

    Two modes, chosen by whether the debate has started consuming turns:

    * ``cursor == 0`` — the debate hasn't begun, so a full rebuild is safe:
      :meth:`DebateState.build` is authoritative for ordering.
    * ``cursor > 0`` — at least one ``debate next-turn`` has already been
      consumed. A full rebuild would change which logical turn ``cursor``
      indexes into (because the flat schedule interleaves rounds across PRs:
      all PRs' round-0 slots come before any round-1 slot). So we *append*
      schedule slots for genuinely-new PRs at the tail and leave already-
      indexed slots untouched. Existing PRs are unchanged.

    AGENTS.md's documented sprint loop opens every PR *before* the first
    ``next-turn``, so the append-only path exists for resilience rather than
    routine use.
    """
    if debate.cursor == 0:
        fresh = DebateState.build(cfg, sprint_prs)
        debate.schedule = fresh.schedule
        debate.pr_ids = fresh.pr_ids
        return

    known_ids = set(debate.pr_ids)
    new_prs = [p for p in sprint_prs if p.id not in known_ids]
    if not new_prs:
        return
    appended = DebateState.build(cfg, new_prs).schedule
    debate.schedule.extend(appended)
    debate.pr_ids.extend(p.id for p in new_prs)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    target_dir: Path = typer.Argument(
        ...,
        help="Directory to create for a new isolated agenteam project.",
    ),
) -> None:
    """Scaffold a clean project from the packaged universal blueprints."""
    target = _resolve_target_dir(target_dir)
    if target.exists() and not target.is_dir():
        raise _err(f"target path exists and is not a directory: {target}")
    if target.exists() and any(target.iterdir()):
        raise _err(f"target directory is not empty: {target}")

    templates = _resource_templates_root()
    if not templates.is_dir():
        raise _err("packaged templates are missing; reinstall agenteam")

    try:
        target.mkdir(parents=True, exist_ok=True)
        _copy_resource_tree(templates.joinpath("AGENTS.md"), target / "AGENTS.md")
        _copy_resource_tree(
            templates.joinpath(".claude", "agents"),
            target / ".claude" / "agents",
        )
        _copy_resource_tree(
            templates.joinpath("sprints", "sprint-1.md"),
            target / "sprints" / "sprint-1.md",
        )
    except OSError as e:
        raise _err(f"init failed: {e}") from e

    typer.echo(f"Successfully initialized agenteam project at {target}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(f"  cd {target}")
    typer.echo("  customize AGENTS.md, .claude/agents/, and sprints/sprint-1.md")
    typer.echo("  agenteam bootstrap")
    typer.echo("  open this folder inside your coding agent session")


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


@app.command()
def bootstrap(
    ctx: typer.Context,
    reset: bool = typer.Option(False, "--reset", help="Wipe workspace/ before init."),
) -> None:
    """Initialize workspace/ (inner git repo) and state/ directories. Idempotent."""
    root = _root(ctx)
    ensure_dirs(root)
    try:
        _engine(ctx).bootstrap(reset=reset)
    except GitCommandError as e:
        raise _err(f"bootstrap failed: {e}") from e
    _print_banner()
    typer.echo(f"workspace ready at {root / 'workspace'}")
    typer.echo(f"state ready at {root / 'state'}")


# ---------------------------------------------------------------------------
# sprint
# ---------------------------------------------------------------------------


@sprint_app.command("show")
def sprint_show(ctx: typer.Context, sprint_id: str) -> None:
    """Print the parsed SprintConfig as JSON."""
    cfg = _load_sprint_or_die(_root(ctx), sprint_id)
    typer.echo(cfg.model_dump_json(indent=2))


@sprint_app.command("list")
def sprint_list(ctx: typer.Context) -> None:
    """List all sprints in sprints/."""
    try:
        sprints = list_sprints(_root(ctx))
    except SprintParseError as e:
        raise _err(f"invalid sprint file: {e}") from e
    if not sprints:
        typer.echo("(no sprints)")
        return
    for cfg in sprints:
        typer.echo(
            f"{cfg.id}\t{cfg.title}\tparticipants={','.join(cfg.participants)}"
        )


# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------


@app.command("branch")
def branch_cmd(
    ctx: typer.Context,
    action: str = typer.Argument(..., help="One of: create"),
    name: str = typer.Argument(..., help="Branch name, e.g. feat/cpo/core-app-prd"),
    base: str = typer.Option("main", "--base"),
) -> None:
    """Branch operations (currently: create)."""
    if action != "create":
        raise _err(f"unknown branch action: {action!r} (expected 'create')")
    try:
        path = _engine(ctx).checkout_branch(name, base=base)
    except GitCommandError as e:
        raise _err(f"branch create failed: {e}") from e
    typer.echo(str(path))


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@app.command("read")
def read_cmd(
    ctx: typer.Context,
    branch: str = typer.Option(..., "--branch"),
    path: str = typer.Option(..., "--path"),
) -> None:
    """Print file contents from a branch's worktree."""
    try:
        typer.echo(_engine(ctx).read_file(branch, path), nl=False)
    except FileNotFoundError as e:
        raise _err(f"file not found: {e}") from e


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


@app.command("commit")
def commit_cmd(
    ctx: typer.Context,
    branch: str = typer.Option(..., "--branch"),
    author: str = typer.Option(..., "--author"),
    message: str = typer.Option(..., "--message"),
    files: list[str] = typer.Option(
        ..., "--files", help="Repeat --files for each path"
    ),
) -> None:
    """Stage and commit files on a branch's worktree under the author identity."""
    try:
        sha = _engine(ctx).commit(branch, author, message, files)
    except GitCommandError as e:
        raise _err(f"commit failed: {e}") from e
    typer.echo(sha)


# ---------------------------------------------------------------------------
# pr
# ---------------------------------------------------------------------------


@pr_app.command("open")
def pr_open(
    ctx: typer.Context,
    branch: str = typer.Option(..., "--branch"),
    author: str = typer.Option(..., "--author"),
    title: str = typer.Option(..., "--title"),
    sprint: str = typer.Option(..., "--sprint"),
    files: list[str] = typer.Option([], "--files"),
    description: str = typer.Option("", "--description"),
    base: str = typer.Option("main", "--base"),
) -> None:
    """Open a PR and refresh the sprint's debate schedule.

    Schedule refresh semantics live in :func:`_rebuild_schedule`: full
    rebuild before any turn has been consumed, append-only after the cursor
    advances. Opening the same ``(branch, author, title)`` triple twice is
    rejected via the ``pr_id`` collision check; PRs can't be "updated" in
    place — open a new PR with a different branch instead.
    """
    root = _root(ctx)
    cfg = _load_sprint_or_die(root, sprint)

    if author not in cfg.participants:
        raise _err(
            f"author {author!r} is not a participant of sprint {sprint!r} "
            f"(participants: {', '.join(cfg.participants)})"
        )

    pr_id = f"pr-{_short_id(branch, author, title)}"

    # The outer ``try/except StateLockTimeout`` only ever catches the
    # acquire-side failure of ``state_lock``; everything inside the ``with``
    # block raises ``typer.Exit`` (not a ``StateLockTimeout``) and propagates
    # past this handler unchanged. The nesting reads slightly odd but is
    # intentional.
    try:
        with state_lock(root):
            prs = PRRegistry.load(root)
            if pr_id in prs:
                raise _err(f"PR already exists: {pr_id}")

            pr = PullRequest(
                id=pr_id,
                title=title,
                branch=branch,
                base=base,
                author=author,
                description=description,
                files=list(files),
                status=PRStatus.OPEN,
                sprint_id=sprint,
            )
            prs[pr_id] = pr
            PRRegistry.save(root, prs)

            sprint_prs = sorted(
                [p for p in prs.values() if p.sprint_id == sprint],
                key=lambda p: p.created_at,
            )

            debate = DebateStore.load(root, sprint) or DebateState(sprint_id=sprint)
            _rebuild_schedule(debate, cfg, sprint_prs)
            DebateStore.save(root, debate)
    except StateLockTimeout as e:
        raise _err(str(e)) from e

    typer.echo(pr_id)


@pr_app.command("comment")
def pr_comment(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id"),
    reviewer: str = typer.Option(..., "--reviewer"),
    comment: str = typer.Option(..., "--comment"),
    file: Optional[str] = typer.Option(None, "--file"),
    approve: bool = typer.Option(False, "--approve"),
) -> None:
    """Append a review to a PR and a REVIEW message to the sprint transcript.

    Ordering invariant: the orchestrator is expected to call ``debate
    next-turn`` *before* ``pr comment`` for the resulting review. The
    transcript's ``round_idx`` is attributed to the turn the cursor most
    recently consumed (``cursor - 1``); calling ``pr comment`` without a
    prior ``next-turn`` would record the review under round 0 by default.
    The documented sprint loop in AGENTS.md follows this ordering.
    """
    root = _root(ctx)
    try:
        with state_lock(root):
            prs = PRRegistry.load(root)
            if id not in prs:
                raise _err(f"unknown PR: {id}")
            pr = prs[id]
            if reviewer == pr.author:
                raise _err(f"reviewer {reviewer!r} cannot review own PR {id}")

            review = ReviewComment(
                reviewer=reviewer, file=file, comment=comment, approved=approve
            )
            pr.reviews.append(review)
            if pr.status == PRStatus.OPEN:
                pr.status = PRStatus.UNDER_REVIEW
            prs[id] = pr
            PRRegistry.save(root, prs)

            debate = DebateStore.load(root, pr.sprint_id) or DebateState(
                sprint_id=pr.sprint_id
            )
            # round_idx best-effort: the round of whatever turn the cursor
            # most recently consumed. Falls back to 0 before the first turn.
            round_idx = 0
            if debate.schedule and debate.cursor > 0:
                round_idx = debate.schedule[
                    min(debate.cursor - 1, len(debate.schedule) - 1)
                ].round_idx
            msg = Message(
                id=f"msg-{_short_id(id, reviewer, comment, datetime.now(timezone.utc).isoformat())}",
                sender=reviewer,
                recipient=pr.author,
                msg_type=MessageType.REVIEW,
                content=comment,
                parent_id=id,
                round_idx=round_idx,
            )
            debate.transcript.append(msg)
            DebateStore.save(root, debate)
    except StateLockTimeout as e:
        raise _err(str(e)) from e

    typer.echo(json.dumps({"pr": id, "reviewer": reviewer, "approved": approve}))


@pr_app.command("list")
def pr_list(
    ctx: typer.Context,
    sprint: Optional[str] = typer.Option(None, "--sprint"),
) -> None:
    """Print a tabular view of PRs."""
    root = _root(ctx)
    try:
        with state_lock(root):
            prs = PRRegistry.load(root)
    except StateLockTimeout as e:
        raise _err(str(e)) from e

    rows = sorted(
        (p for p in prs.values() if sprint is None or p.sprint_id == sprint),
        key=lambda p: (p.sprint_id, p.created_at),
    )
    if not rows:
        typer.echo("(no PRs)")
        return
    typer.echo(
        f"{'id':<14} {'sprint':<12} {'author':<6} {'status':<13} "
        f"{'approvals':<9} title"
    )
    for p in rows:
        typer.echo(
            f"{p.id:<14} {p.sprint_id:<12} {p.author:<6} {p.status.value:<13} "
            f"{p.approval_count:<9} {p.title}"
        )


@pr_app.command("merge")
def pr_merge(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id"),
) -> None:
    """Merge an approved PR; mark REJECTED on conflict."""
    root = _root(ctx)
    try:
        with state_lock(root):
            prs = PRRegistry.load(root)
            if id not in prs:
                raise _err(f"unknown PR: {id}")
            pr = prs[id]
            if pr.status in (PRStatus.MERGED, PRStatus.REJECTED):
                raise _err(f"PR {id} already {pr.status.value}")

            cfg = _load_sprint_or_die(root, pr.sprint_id)

            if pr.approval_count < cfg.approval_quorum:
                raise _err(
                    f"PR {id} has {pr.approval_count} approvals "
                    f"(quorum is {cfg.approval_quorum})"
                )

            try:
                merge_sha = _engine(ctx).merge_branch(pr.branch, base=pr.base)
            except MergeConflict as e:
                pr.status = PRStatus.REJECTED
                pr.conflict_details = e.details
                prs[id] = pr
                PRRegistry.save(root, prs)
                raise _err(f"merge conflict: {e.details}", code=2) from e
            except GitCommandError as e:
                raise _err(f"merge failed: {e}") from e

            pr.status = PRStatus.MERGED
            prs[id] = pr
            PRRegistry.save(root, prs)
    except StateLockTimeout as e:
        raise _err(str(e)) from e

    typer.echo(merge_sha)


# ---------------------------------------------------------------------------
# debate
# ---------------------------------------------------------------------------


@debate_app.command("next-turn")
def debate_next_turn(
    ctx: typer.Context,
    sprint: str = typer.Option(..., "--sprint"),
) -> None:
    """Return the next (round, speaker, pr_id) tuple or {done: true}.

    Advance-then-save ordering is deliberate: if ``DebateStore.save`` raises
    (disk full, permission flip), the in-memory ``cursor`` mutation is
    discarded with the process and the next CLI invocation re-reads the old
    cursor, replaying the same turn. That's the correct recovery contract —
    a turn is "consumed" only after it has been durably persisted.
    """
    root = _root(ctx)
    try:
        with state_lock(root):
            debate = DebateStore.load(root, sprint)
            if debate is None or not debate.schedule:
                typer.echo(json.dumps({"done": True, "reason": "no schedule"}))
                return

            turn = debate.current_turn()
            if turn is None:
                typer.echo(json.dumps({"done": True}))
                return

            debate.advance()
            DebateStore.save(root, debate)
    except StateLockTimeout as e:
        raise _err(str(e)) from e

    typer.echo(
        json.dumps(
            {
                "round": turn.round_idx,
                "speaker": turn.speaker,
                "pr_id": turn.target_pr_id,
            }
        )
    )


@debate_app.command("tail")
def debate_tail(
    ctx: typer.Context,
    sprint: str = typer.Option(..., "--sprint"),
    window: int = typer.Option(6, "--window"),
) -> None:
    """Print the last N transcript messages as JSON."""
    root = _root(ctx)
    try:
        with state_lock(root):
            debate = DebateStore.load(root, sprint)
    except StateLockTimeout as e:
        raise _err(str(e)) from e

    if debate is None:
        typer.echo(json.dumps([]))
        return
    tail = debate.transcript[-window:] if window > 0 else []
    typer.echo(
        json.dumps([json.loads(m.model_dump_json()) for m in tail], indent=2)
    )


# ---------------------------------------------------------------------------
# human-gate
# ---------------------------------------------------------------------------


@app.command("human-gate")
def human_gate(
    message: str = typer.Option(..., "--message"),
) -> None:
    """Print a banner and block on stdin until the human presses Enter."""
    bar = "=" * 72
    typer.echo(f"\n{bar}")
    typer.echo(f"  HUMAN GATE: {message}")
    typer.echo(f"{bar}")
    try:
        input("press Enter to continue: ")
    except EOFError:
        # Non-interactive caller (CI, scripted orchestrator): treat as
        # immediate continuation. The banner is still printed so the log
        # records the gate was passed through.
        typer.echo("(no tty — auto-continuing)")


# ---------------------------------------------------------------------------
# adr
# ---------------------------------------------------------------------------


@adr_app.command("record")
def adr_record(
    ctx: typer.Context,
    sprint: str = typer.Option(..., "--sprint"),
) -> None:
    """Compose and commit an ADR summarising the sprint's outcome."""
    root = _root(ctx)
    cfg = _load_sprint_or_die(root, sprint)

    try:
        with state_lock(root):
            prs = PRRegistry.load(root)
            sprint_prs = sorted(
                [p for p in prs.values() if p.sprint_id == sprint],
                key=lambda p: p.created_at,
            )
            if not sprint_prs:
                raise _err(f"no PRs found for sprint {sprint}")

            merged = [p for p in sprint_prs if p.status == PRStatus.MERGED]
            rejected = [p for p in sprint_prs if p.status == PRStatus.REJECTED]

            options = [
                f"{p.author.upper()}: {p.title} (status={p.status.value})"
                for p in sprint_prs
            ]
            decision_lines: list[str] = []
            if merged:
                decision_lines.append("Merged:")
                decision_lines.extend(f"  - {p.title} ({p.id})" for p in merged)
            if rejected:
                decision_lines.append("Rejected:")
                decision_lines.extend(
                    f"  - {p.title} ({p.id}): {p.conflict_details or 'no quorum'}"
                    for p in rejected
                )
            decision = "\n".join(decision_lines) or "(no decisions recorded)"

            signoffs = sorted(
                {
                    r.reviewer
                    for p in merged
                    for r in p.reviews
                    if r.approved
                }
            )

            # Status reflects what actually happened in the debate:
            #   - at least one PR merged → "Accepted"
            #   - no merges, at least one rejection → "Rejected"
            #   - everything stuck in review (no merges, no rejections) →
            #     "Proposed" (ADR recorded but outcome is provisional)
            if merged:
                adr_status = "Accepted"
            elif rejected:
                adr_status = "Rejected"
            else:
                adr_status = "Proposed"

            adr_id = f"adr-{_short_id(sprint, *[p.id for p in sprint_prs])}"
            record = DecisionRecord(
                id=adr_id,
                title=f"{cfg.title} — outcome",
                sprint_id=sprint,
                context=cfg.body.strip() or cfg.title,
                options=options,
                decision=decision,
                consequences=(
                    "Downstream sprints inherit the merged artifacts via "
                    "`prerequisite_files`. Rejected PRs leave their branches "
                    "intact for follow-up iterations."
                ),
                signoffs=signoffs,
                status=adr_status,
            )

            eng = _engine(ctx)
            try:
                eng.write_file("main", f"governance/{adr_id}.md", record.to_markdown())
                sha = eng.commit(
                    branch="main",
                    author="ceo",
                    message=f"ADR: {record.title}",
                    files=[f"governance/{adr_id}.md"],
                )
            except GitCommandError as e:
                raise _err(f"failed to commit ADR: {e}") from e
    except StateLockTimeout as e:
        raise _err(str(e)) from e

    typer.echo(json.dumps({"adr_id": adr_id, "sha": sha}))


# ---------------------------------------------------------------------------
# state maintenance
# ---------------------------------------------------------------------------


@state_app.command("unlock")
def state_unlock(ctx: typer.Context) -> None:
    """Force-clear the state lock directory (use only after a crashed holder)."""
    cleared = clear_stale_lock(_root(ctx))
    if cleared:
        typer.echo("lock cleared")
    else:
        typer.echo("no lock to clear")


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------


def main() -> None:  # pragma: no cover - thin shim
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
