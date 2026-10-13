# Development guide

Eva 2 is actively developed in `eva2/`. Start with [AGENTS.md](AGENTS.md), the
[project vision](eva2/PROJECTDOEL.md) and the [current workboard](eva2/WERKBORD.md).
Owner-facing plans and historical decisions currently remain in Dutch.

## Shared source and integration

Start a feature branch from the current `main` integration commit. Confirm that
the application, tests, dependency locks and relevant frontend sources exist
before making changes. Separate worktrees share a base commit, not uncommitted
files. Return a focused feature commit; do not copy another worktree wholesale.
The coordinator integrates shared files and publishes reviewed changes.

Keep one owner per changed component. Reuse existing services and lifecycle
contracts; channel adapters should handle input, output, authentication and
presentation rather than implement separate conversation or task policies.
For the configured WSL checkout, run Git inside WSL.

## Language

Use English for new identifiers, comments, docstrings, test descriptions and
developer documentation. Preserve public API and schema names. Translate
existing explanatory text when editing the relevant component; do not rename
working contracts or rewrite historical evidence solely for language consistency.

Conversation prompts and user-facing text may intentionally be Dutch. Keep the
user's language separate from implementation language. A complete configurable
localization system is not claimed to exist yet.

## Validation and delivery

Use the dependency locks and existing component test environment. Run tests
appropriate to the changed behavior and its relevant integration boundaries.
Database tests require the documented disposable database, never live household
data. See the relevant test fixtures and dated handoff instructions for setup.

The workboard JSON is the source for the human workboard and dashboard data.
From `eva2/`, validate it with `python tools/check_current_workboard.py`.
Record built, tested, reviewed, deployed and owner-accepted status separately.
Do not repeat provider calls when existing evidence still applies.

## Public release preparation

The English entry points are a starting point, not a complete public release.
An owner-selected project license, English installation documentation and review
of household-specific configuration and historical evidence remain separate
release work. Existing third-party license notices must be retained.
