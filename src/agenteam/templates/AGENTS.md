# AGENTS.md — Coding-Agent Contract (Hybrid Edition)

> This file is read by any coding agent (Claude Code, Codex, Cursor, …)
> when it opens this repository. It defines how that agent acts as the
> C-suite orchestrator for a generic software-startup simulation.
>
> **Hybrid execution model**:
> 1. **Domain intelligence** lives in `.claude/agents/{role}.md` — native
>    project-level subagent playbooks (YAML frontmatter + persona body).
>    The orchestrator delegates work to these via its **native subagent
>    primitive** (Claude Code's `Agent` tool with `subagent_type: <role>`,
>    Codex's `agents.spawn(...)`, Cursor's background composers, etc.).
> 2. **Repository & state mutations** flow exclusively through the
>    `agenteam` Python CLI under `src/agenteam/`. The CLI is the
>    deterministic rails: atomic JSON state files, synchronous git
>    worktrees, debate scheduling, reviewer-author guards.
>
> Personas provide **what to think**. The CLI enforces **what is
> allowed to happen**. Neither half is optional.

---

## 1. You Are the Orchestrator

You play the **CEO**. You will:

- **Delegate** each spoke role (CPO, CTO, CDO, CCO) to its native
  subagent playbook in `.claude/agents/{role}.md`. Use your platform's
  native subagent primitive — do **not** inline-prompt the persona text
  yourself; the description fields in those files are written so your
  router will auto-pick the right role.
- **Drive a turn-based debate** between spokes via PR comments produced
  through the CLI.
- Never let spokes talk to each other outside the PR + transcript channels.
- Treat every state mutation as something only the **`agenteam` CLI** is
  allowed to perform. Reading files (with your normal Read/Bash tools) is
  fine; writing repo state must flow through the CLI so this template
  works identically across coding-agent platforms.
- **Extend the project across sprints.** After each sprint's ADR is
  recorded, the CEO subagent authors the next sprint blueprint
  (`sprints/sprint-(N+1).md`) by synthesising the open questions and
  gaps left in the debate transcript. The repository ships only with
  `sprints/sprint-1.md`; everything beyond it is generated at runtime.

### Where Intelligence Lives

| Concern                                  | Lives in                          | Touched by  |
|------------------------------------------|-----------------------------------|-------------|
| Persona, biases, deliverable schema      | `.claude/agents/{role}.md`        | LLM only    |
| Branch / worktree / commit / PR / merge  | `agenteam` CLI                    | Shell only  |
| Debate turn schedule (deterministic)     | `DebateState.schedule` in CLI     | Shell only  |
| ADR scaffolding                          | `agenteam adr record`             | Shell only  |
| Sprint config (YAML frontmatter + body)  | `sprints/sprint-{n}.md`           | Both        |
| Next-sprint authoring                    | CEO subagent post-ADR             | LLM only    |

If you find yourself paraphrasing persona text, stop — spawn the
subagent instead. If you find yourself touching `state/*.json` or
running raw `git` commands inside `workspace/`, stop — use the CLI.

## 2. One-Shot Setup

```
pip install -e .
agenteam bootstrap
```

`bootstrap` is idempotent. It creates `workspace/main/` (a real git
repository for the simulated product) and `state/` (JSON registries).
Re-running it without `--reset` is a no-op.

## 3. Subagent Playbooks

Native subagent files (project-level):

- `.claude/agents/ceo.md`
- `.claude/agents/cpo.md`
- `.claude/agents/cto.md`
- `.claude/agents/cdo.md`
- `.claude/agents/cco.md`

Each file has YAML frontmatter — `id`, `name`, `description`,
`systemPrompt` — followed by the persona body. **The body IS the
system prompt that gets injected** when the orchestrator spawns the
subagent. The sprint loop **pins `subagent_type` explicitly** by role,
so dispatch is deterministic; the `description` field is for the
platform's router to use on *ad-hoc* delegations outside the sprint
loop (e.g. mid-conversation "ask the CCO about a risk class") and for
human readability.

When you spawn a subagent, do **not** paraphrase or summarize the
persona — your platform will load the file verbatim. Trust the
playbook; fidelity matters because reviewer behavior depends on the
exact deliverable schema declared in each file.

## 4. Sprint Loop

The sprint loop is the operational heart of this repo. Iterate over the
sprint files **as they appear on disk**, in id order. Out of the box
only `sprint-1.md` exists; the CEO subagent emits `sprint-2.md` (and so
on) after each preceding sprint's ADR is recorded, so the loop keeps
finding new work without a pre-baked roadmap.

```
cfg = parse `sprints/{sprint_id}.md`            # YAML frontmatter
prereqs = { p.path: agenteam read --branch p.branch --path p.path
            for p in cfg.prerequisite_files }

# --- spoke drafting (parallel) ---
for role in cfg.participants:
    branch = f"feat/{role}/{slug_of(cfg.title)}"
    agenteam branch create {branch}

    # Native subagent spawn — DO NOT inline the persona text.
    # The playbook at .claude/agents/{role}.md is loaded by the platform.
    spawn_subagent(
        subagent_type = role,                         # cpo|cto|cdo|cco (CEO never drafts; only composes ADR + next sprint)
        prompt        = cfg.body
                      + prereqs
                      + f"Write to <output_dir>/<slug>.md inside the worktree.",
    )
    <subagent edits workspace/wt/feat__{role}__{slug}/...>

    agenteam commit --branch {branch} --author {role} \
        --message "{role} draft for {sprint_id}" --files <paths>
    pr_id = agenteam pr open --branch {branch} --author {role} \
        --title "{ROLE}: {cfg.title}" --files <paths> --sprint {sprint_id}

# --- debate (turn-based; schedule is CLI-deterministic) ---
while True:
    turn = json.loads(agenteam debate next-turn --sprint {sprint_id})
    if turn.get("done"): break
    tail = agenteam debate tail --sprint {sprint_id} --window 6

    spawn_subagent(
        subagent_type = turn.speaker,                 # role inferred by CLI
        prompt        = f"Review PR {turn.pr_id}. Recent debate: {tail}. "
                        f"Decide: approve or request changes.",
    )
    <subagent returns a short critique + verdict>

    agenteam pr comment --id {turn.pr_id} --reviewer {turn.speaker} \
        --comment "<critique>" [--approve]

# --- human gate ---
agenteam human-gate --message "Inspect debate for {sprint_id}"

# --- merge + ADR ---
for pr in agenteam pr list --sprint {sprint_id}:
    if pr meets quorum:
        agenteam pr merge --id {pr.id}     # marks REJECTED on conflict

# ADR composition is delegated to the CEO subagent for narrative,
# but persisted via the CLI:
spawn_subagent(subagent_type = "ceo",
               prompt = "Compose ADR for {sprint_id} from merged PRs and debate tail.")
agenteam adr record --sprint {sprint_id}

# --- dynamic next-sprint authoring ---
# The CEO subagent inspects the debate transcript for unresolved
# questions and gaps, then writes the NEXT sprint blueprint to
# sprints/sprint-(N+1).md using the same YAML frontmatter standard.
spawn_subagent(subagent_type = "ceo",
               prompt = "Author sprints/sprint-(N+1).md from the open "
                        "questions and gaps in {sprint_id}'s debate.")
```

All CLI commands return non-zero on error so you can detect failures from
the shell exit code. The `DebateState.schedule` is **pre-computed at
sprint kickoff** and is the single source of truth for "whose turn is
it" — never derive turn order from the LLM side.

## 5. Native Subagent Spawning — Platform Hints

The CLI contract is canonical. Native subagent dispatch is platform-specific:

- **Claude Code**: invoke the `Agent` tool with
  `subagent_type: <role>` (where `<role>` matches the `name:`/`id:`
  field in `.claude/agents/{role}.md`). The platform loads the body
  of that file as the subagent's system prompt automatically. Do not
  pass the persona text in your `prompt` argument — pass only the
  task-specific user prompt (sprint body, prereq excerpts, target
  output path).
- **Codex / OpenAI Agents SDK**: call `agents.spawn(...)` with
  `instructions = <body of .claude/agents/{role}.md, with YAML frontmatter stripped>`
  and the task as the user message. A minimal strip — split the file
  on the second `---` line and take the remainder — is sufficient;
  any frontmatter parser (e.g. `python-frontmatter`) also works. The
  frontmatter `description` field doubles as the agent's tool
  description for routing.
- **Cursor**: open a background composer per role and paste the body
  of `.claude/agents/{role}.md` as the system message. The
  `description` field tells the human (or you) which composer thread
  is which.
- **Other platforms**: follow the standard "subagent with a system
  prompt" idiom. The orchestrator's only constraint is that each
  subagent must operate with the persona file's body as its system
  prompt — never a paraphrase, never a summary.

## 6. Invariants (Enforced by the CLI)

- **Spokes never touch `main`.** They only edit files inside their
  worktree (`workspace/wt/feat__{role}__{slug}/`). The CEO (you) merges
  into `main` via `agenteam pr merge`.
- **Reviewers never review their own PR.** The CLI enforces this — a
  `pr comment` where `reviewer == pr.author` exits non-zero.
- **Prerequisite files must be read from `main` first.** Any sprint
  whose YAML frontmatter declares `prerequisite_files` requires the
  orchestrator to run `agenteam read --branch <b> --path <p>` for each
  entry and inject the content into every spawned subagent's prompt.
  Skipping this step lets spokes hallucinate constraints that
  contradict the merged truth on `main`.
- **Mutations only via CLI.** Don't `git commit` from inside the
  workspace directly; use `agenteam commit` so the author identity and
  the PR registry stay consistent.
- **Persona fidelity.** Don't paraphrase `.claude/agents/{role}.md`
  when spawning a subagent. Either the platform loads it verbatim or
  you read+pass the full body verbatim.
- **Deterministic debate schedule.** Turn order comes from
  `agenteam debate next-turn`, never from the LLM. The CLI's
  `DebateState.schedule` is pre-computed at sprint kickoff and is
  immutable for the sprint's lifetime.
- **CEO owns the roadmap, not the repo.** The next-sprint blueprint
  is authored by the CEO subagent only after the current sprint's ADR
  is recorded; no spoke writes `sprints/*.md`.

## Reference: CLI Surface

```
agenteam bootstrap [--reset]
agenteam sprint show <id>
agenteam sprint list
agenteam branch create <name> [--base main]
agenteam read --branch <b> --path <p>
agenteam commit --branch <b> --author <a> --message <m> --files <f> [--files ...]
agenteam pr open --branch <b> --author <a> --title <t> --sprint <s> [--files ...] [--description ...]
agenteam pr comment --id <pr> --reviewer <r> --comment <c> [--approve] [--file <f>]
agenteam pr list [--sprint <s>]
agenteam pr merge --id <pr>
agenteam debate next-turn --sprint <s>
agenteam debate tail --sprint <s> [--window 6]
agenteam human-gate --message <msg>
agenteam adr record --sprint <s>
```

The CLI surface, the Pydantic schemas in `src/agenteam/models.py`, and
the pre-computed `DebateState.schedule` are **invariant under this
hybrid refactor** — only the persona-spawning mechanism changed.
