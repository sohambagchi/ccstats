# ccstats hub: archive, parser, API and dashboard

- `ccstats collect | compute | report | sessions | check | serve | host | pull | script` (Python stdlib, nix flake package and dev shell).
- Parser turns each transcript into sessions/subagents, deduplicated API messages (tokens incl. thinking and cache), tool calls (Bash executable, errors, background, human-approved), human prompts and per-day Claude/API/agent time; SQLite store re-parses only changed files.
- Web dashboard at `/`: Claude time and token charts, tool and Bash top lists, per-host table, recent sessions; filters for range, bucket, host, profile, project.
- JSON API: `/api/summary`, `/api/tools`, `/api/models`, `/api/sessions`, `/api/dimensions`, `/api/hosts`, `/api/health`, `POST /api/refresh`.
- Remote helpers served by the hub: `/push.sh` (key, timer, retention setting, rsync push) and `/cloudlab-setup.sh`; `ccstats host add|rm|ls` manages restricted push keys.
- `deploy/install-hub.sh` installs `ccstats-serve.service` and `ccstats-collect.timer` from a pinned build.
- On real spore data, transcript token sums never exceed Claude Code's `cost-state`, and one session matches it exactly.
- spore's `~/.claude/settings.json` now sets `cleanupPeriodDays: 3650` (backup `settings.json.bak-ccstats`).
- Rationale: see ADRs [2026-11-07-0159-archive-raw-then-compute](../adr/2026-11-07-0159-archive-raw-then-compute.md), [2026-11-07-0159-push-over-rsync-with-restricted-keys](../adr/2026-11-07-0159-push-over-rsync-with-restricted-keys.md), [2026-11-07-0159-cloudlab-transcripts-on-proj](../adr/2026-11-07-0159-cloudlab-transcripts-on-proj.md), [2026-11-07-0159-hub-web-server-open-reads](../adr/2026-11-07-0159-hub-web-server-open-reads.md), [2026-11-07-0159-claude-time-definition](../adr/2026-11-07-0159-claude-time-definition.md)
