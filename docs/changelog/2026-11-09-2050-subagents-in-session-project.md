# Subagents count toward their session's project

- A subagent's project is its session's, set at rollup; worktree agents (`agent-<id>`) no longer appear as projects of their own and the project list drops from 49 to 28 names
- Charts, tiles and `by=project` now agree with the sessions list under a project filter
- `meta.rollup` (`ROLLUP_VERSION`) re-rolls every session once after a rollup change, without re-parsing; this one ran in 0.6 s
