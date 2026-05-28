```
 █████╗  ██████╗ ███████╗███╗   ██╗███████╗██╗   ██╗██╗████████╗███████╗
██╔══██╗██╔════╝ ██╔════╝████╗  ██║██╔════╝██║   ██║██║╚══██╔══╝██╔════╝
███████║██║  ███╗█████╗  ██╔██╗ ██║███████╗██║   ██║██║   ██║   █████╗
██╔══██║██║   ██║██╔══╝  ██║╚██╗██║╚════██║██║   ██║██║   ██║   ██╔══╝
██║  ██║╚██████╔╝███████╗██║ ╚████║███████║╚██████╔╝██║   ██║   ███████╗
╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝   ╚═╝   ╚══════╝
```

A **universal multi-agent C-suite template** for executing structured,
evolutionary sprints inside any generic software startup. A coding agent
(Claude Code, Codex, Cursor, …) clones this repo, reads `AGENTS.md`, and
then *acts out* a C-suite simulation: it spawns one subagent per role
(CEO, CPO, CTO, CDO, CCO), drives a turn-based debate via pull requests,
and produces real git artifacts in a sandboxed inner repository.

The framework is **idea-agnostic**. Drop your product thesis into the
sprint body and the C-suite will reason about it; nothing in the
template assumes a domain. The CEO subagent extends the project itself
by authoring the next sprint blueprint after each ADR, so the loop
keeps running without a hand-curated roadmap.

There is **no LLM client inside this codebase**. The orchestrator is the
coding agent itself; the Python code is plumbing — schemas, a git
subprocess wrapper, JSON state files, and a thin CLI that owns every
state mutation.

## Quickstart

```bash
pip install -e .
agensuite bootstrap
```

Then open the repo in any coding agent and prompt it with:

> Read AGENTS.md and execute sprint-1.

The agent will follow the contract in `AGENTS.md`, spawn role subagents,
drive the debate, pause at the human gate for you, merge the approved
PRs, write an ADR, and then ask the CEO subagent to draft the next
sprint blueprint into `sprints/sprint-2.md`.

## Layout

| Path | Purpose |
|------|---------|
| `AGENTS.md` | Contract read by any coding-agent platform on setup. |
| `.claude/agents/{role}.md` | Native subagent playbooks — one per C-suite role (YAML frontmatter + persona body). |
| `sprints/sprint-*.md` | Sprint definitions (YAML frontmatter + body). Ships with `sprint-1.md` only; subsequent sprints are authored by the CEO at runtime. |
| `src/agensuite/` | Pydantic schemas, git engine, state, CLI. |
| `workspace/` (gitignored) | The inner simulated git repo. |
| `state/` (gitignored) | JSON registries for PRs and debate transcripts. |

## CLI

Run `agensuite --help` to see every subcommand, or jump to the reference
section at the bottom of `AGENTS.md`.

## License

MIT — see `LICENSE`.
