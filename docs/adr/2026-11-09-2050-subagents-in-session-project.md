# Subagents count toward their session's project

**Status:** accepted

**Context:** A thread's project was the last component of its own `cwd`. Subagents running in a worktree work in `<repo>/.claude/worktrees/agent-<id>`, so each became a one-thread project (18 of 49 project names). A few others ran in another repo (`pdg` inside `ka` sessions, `app` inside the course sessions). Filters match `threads.project` while the sessions list uses the session's project, so filtering `hamlet` left those subagents out of the charts ($387 over 32 threads) but kept them in the session totals.
**Decision:** `rollup_session` sets every subagent's `project` to its main thread's; `threads.cwd` keeps where it ran. It runs at rollup, not parse, so a subagent that arrives before its main transcript keeps its own name until the main thread's arrival rolls the session up again. `meta.rollup` carries `ROLLUP_VERSION`; a mismatch rolls up every session once on the next refresh, without re-parsing.
**Alternatives:** strip only the `.claude/worktrees/<id>` suffix from `cwd`: fixes the worktree names but keeps the filter mismatch for subagents working in another repo. Attribute by directory everywhere, including sessions: a session would then have no single project.
**Consequences:** the project list tracks real repos (49 → 28); a project filter gives the same total in charts and in the sessions list. Work a subagent did in another repo is no longer visible as that repo's; the session view still shows its `cwd`. The one-time re-rollup took 0.6 s for 141 sessions.
