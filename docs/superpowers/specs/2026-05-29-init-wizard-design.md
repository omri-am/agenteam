# Design: Guided `init` Wizard

**Date:** 2026-05-29
**Status:** Approved (pre-implementation)

## Problem

`agensuite init` today scaffolds template files, substitutes the `--idea`
string into two tokens, then prints:

```
customize AGENTS.md, .claude/agents/, and sprints/sprint-1.md
```

This pushes the user to hand-edit files. The only other customization path,
`agensuite chief customize <role> --focus "..."`, is a long one-liner per
persona. Both flows force the user to either open files or type verbose
commands.

**Goal:** replace the "go edit files yourself" step with a guided interactive
CLI wizard. The user answers questions in a polished terminal flow; the wizard
writes the answers into the scaffolded files. No file opening, no long
one-liners — including the initial idea prompt.

## Non-Goals

- New metadata tokens (company name, tagline, target market) — deferred; no
  template changes this round.
- Full-screen TUI (Textual) or spawning a separate terminal window.
- Removing or changing `agensuite chief customize` — it stays for post-init
  tweaks.

## UX Decisions (locked during brainstorming)

- **UI style:** inline guided prompts in the same terminal running
  `agensuite init` (arrow-key selects, validated free-text, step headers).
- **Library:** `questionary` (arrow-key `select`/`checkbox`/`text`/`confirm`).
  Its engine `prompt_toolkit` is already present in the environment. One new
  runtime dependency.
- **Bias flow:** per-persona, multi-line, skippable. For each of
  CEO/CPO/CTO/CDO/CCO, show the role + a one-line ownership blurb, accept any
  number of bias lines (blank line finishes that persona), allow skipping the
  persona entirely.
- **Fields collected:** startup idea, per-persona biases, sprint-1 config.
  (Company metadata explicitly skipped.)

## Flow

```
agensuite init my-startup
  1. validate target dir (empty / creatable)   ← BEFORE any prompts
  2. Step 1/3 · Idea      text prompt            (skipped if --idea passed)
  3. Step 2/3 · Personas  per role: blurb + N bias lines (blank=done; skippable)
  4. Step 3/3 · Sprint-1  debate_rounds(default 2) · approval_quorum(default 2)
                          · participants (checkbox: cpo/cto/cdo/cco, default all)
  5. confirm summary → proceed?
  6. scaffold templates + substitute idea token
  7. append bias bullets per persona
  8. rewrite sprint-1.md YAML frontmatter
  9. success + next steps (the "customize files" line is removed)
```

Target-dir validation runs first so the user never answers a full wizard only
to hit "target directory is not empty".

## Architecture

### New module: `src/agensuite/wizard.py`

- `run_init_wizard(*, idea: str | None) -> InitAnswers`
- The **only** place `questionary` is imported/called. Keeps the prompt layer
  isolated and the rest of the code prompt-free and unit-testable.
- Returns an `InitAnswers` pydantic model:

```python
class InitAnswers(BaseModel):
    idea: str
    biases: dict[str, list[str]]   # role -> bias lines (roles with none omitted)
    debate_rounds: int
    approval_quorum: int
    participants: list[str]        # subset/order of [cpo, cto, cdo, cco]
```

- A small static `role -> one-line blurb` map drives the persona display
  (e.g. `cto -> "infra & latency"`). Hardcoded in the wizard to match the
  approved mock; kept short.

### Pure appliers (no prompt I/O — the tested core)

- `_substitute_tokens(text, idea)` — exists, reused.
- `_append_operational_bias(content, focus)` — exists, reused (loop per bias
  line per role).
- **`_set_sprint_frontmatter(content, *, rounds, quorum, participants) -> str`**
  — new. Parses the leading YAML frontmatter of `sprint-1.md` with `pyyaml`
  (already a dependency), updates `debate_rounds` / `approval_quorum` /
  `participants`, re-serializes, preserves the markdown body verbatim.
  Validates `rounds >= 1` and `1 <= quorum <= len(participants)`; raises
  `ValueError` on violation.

### `init` orchestration

1. Resolve + validate target dir (current logic, moved before the wizard).
2. Obtain answers:
   - **Interactive** (stdin is a TTY and no `--idea`): `run_init_wizard(idea=None)`.
   - **Non-interactive** (stdin not a TTY, or `--idea` supplied): build
     `InitAnswers` from `--idea` + defaults (rounds=2, quorum=2,
     participants=all four, no biases). No prompts → never hangs.
3. Scaffold templates into target (current `_copy_resource_tree`), substituting
   the idea token.
4. Apply biases: for each role in `answers.biases`, append each line under that
   persona's `## Operational Biases` section.
5. Apply sprint config: rewrite `sprints/sprint-1.md` frontmatter.
6. Print success + trimmed next steps (no "customize files" line; keep
   `cd`, `agensuite bootstrap`, "open this folder in your coding agent").

## Non-Interactive / TTY Handling

The wizard is bypassed entirely when `sys.stdin.isatty()` is false **or**
`--idea` is provided. This keeps:

- the existing `init --idea "..."` test path working unchanged,
- CI and scripted/headless invocations from blocking on prompts,
- piped input from being mis-parsed by `questionary`.

## Testing

- **`_set_sprint_frontmatter`** — unit tests: happy path updates the three
  fields and preserves body; quorum > participants raises; rounds < 1 raises.
- **Bias application** — `_append_operational_bias` already covered; add a
  test that multiple lines for one role append in order.
- **Non-TTY `init`** — CliRunner smoke test with `--idea` asserts files
  scaffolded, idea substituted, defaults applied, no hang.
- **Wizard** — kept thin; not driven through a live TTY in tests. The bulk of
  behavior lives in the pure appliers, which are tested directly with
  constructed `InitAnswers`.

## Dependencies

- Add `questionary` to `[project] dependencies` in `pyproject.toml`.

## Risks / Notes

- `questionary` needs a TTY; the non-interactive guard above is the mitigation.
- Persona blurb map is a small duplication of each agent file's `description`
  frontmatter; accepted for simplicity. If drift becomes a concern later, read
  the `description` field from the template files instead.
