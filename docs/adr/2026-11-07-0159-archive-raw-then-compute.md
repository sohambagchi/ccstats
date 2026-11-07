# Archive raw transcripts; stats are a pure function of the archive

**Status:** accepted

**Context:** Claude Code deletes transcripts after `cleanupPeriodDays` (default 30), and the parser will keep changing as the transcript format does.
**Decision:** Collection only copies `projects/` trees (`*.jsonl`, `*.json` sidecars; no `tool-results/`, `memory/`, `file-history/`) into `~/.local/share/ccstats/archive/<host>/<profile>/projects/` with rsync and never `--delete`. `compute` re-parses changed files (keyed on size, mtime, parser version, timezone, approval cap) into SQLite. `cleanupPeriodDays: 3650` is set on every host as a second net.
**Alternatives:** parse on each host and ship rows — loses the raw data, so parser fixes can't be applied retroactively.
**Consequences:** the archive only grows (~20 MB/month per busy host). Bumping `PARSER_VERSION` or `compute --full` rebuilds every number. A forked session repeats records, so messages, tool calls, prompts and busy slices are de-duplicated by id across files (`u_*` views).
