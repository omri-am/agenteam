"""agensuite — agent-native C-suite template.

Plumbing only — no LLM client lives in this package. A coding-agent platform
(Claude Code / Codex / Cursor / ...) drives the workflow by shelling out to
the ``agensuite`` CLI defined in :mod:`agensuite.cli`. The orchestration
contract lives in ``AGENTS.md`` at the repo root.

The exports below are the stable public surface for in-process callers
(tests, alternate frontends). Anything not re-exported here is an
implementation detail and may change without notice.
"""

from __future__ import annotations

from .git_engine import GitCommandError, GitEngine, MergeConflict
from .models import (
    Commit,
    DebateState,
    DebateTurn,
    DecisionRecord,
    Message,
    MessageType,
    PRStatus,
    PullRequest,
    ReviewComment,
    SprintConfig,
    SprintPrerequisite,
)
from .sprint_loader import SprintParseError, list_sprints, load_sprint, parse_sprint
from .state import (
    DebateStore,
    PRRegistry,
    StateLockTimeout,
    clear_stale_lock,
    ensure_dirs,
    state_lock,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # git_engine
    "GitCommandError",
    "GitEngine",
    "MergeConflict",
    # models
    "Commit",
    "DebateState",
    "DebateTurn",
    "DecisionRecord",
    "Message",
    "MessageType",
    "PRStatus",
    "PullRequest",
    "ReviewComment",
    "SprintConfig",
    "SprintPrerequisite",
    # sprint_loader
    "SprintParseError",
    "list_sprints",
    "load_sprint",
    "parse_sprint",
    # state
    "DebateStore",
    "PRRegistry",
    "StateLockTimeout",
    "clear_stale_lock",
    "ensure_dirs",
    "state_lock",
]
