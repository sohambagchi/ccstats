# README stats table refreshed on commit

- `.githooks/pre-commit` rewrites a 30-day table at the top of `README.md` (Claude time, est. cost, sessions, subagents, tool calls, output tokens) from `ccstats report --days 30 --total --json` and stages it
- The table lives between `<!-- stats:begin -->` / `<!-- stats:end -->` markers; the hook never blocks a commit when `ccstats` or its data is missing
- Enable once per clone: `git config core.hooksPath .githooks`
