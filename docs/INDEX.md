# Index

Console: https://claude.ai/artifact/SfziVxpWd83cbuxQEFcy1c

## Analysis

- [2026-11-10-0051-http-api-reference](analysis-artifacts/2026-11-10-0051-http-api-reference.md) — how to query the HTTP API, and the Python client

## Decisions

- [2026-11-07-0159-archive-raw-then-compute](adr/2026-11-07-0159-archive-raw-then-compute.md) — append-only archive; stats recomputed from it
- [2026-11-07-0159-push-over-rsync-with-restricted-keys](adr/2026-11-07-0159-push-over-rsync-with-restricted-keys.md) — push hosts rsync with rrsync-restricted keys
- [2026-11-07-0159-cloudlab-transcripts-on-proj](adr/2026-11-07-0159-cloudlab-transcripts-on-proj.md) — CloudLab transcripts live on /proj, hub pulls
- [2026-11-07-0159-hub-web-server-open-reads](adr/2026-11-07-0159-hub-web-server-open-reads.md) — stdlib HTTP API + dashboard, IP allowlist, no artifact job
- [2026-11-07-0159-claude-time-definition](adr/2026-11-07-0159-claude-time-definition.md) — what Claude time measures
- [2026-11-09-1945-precomputed-session-rollups](adr/2026-11-09-1945-precomputed-session-rollups.md) — per-session numbers computed once per transcript change; API answers cached by data version
- [2026-11-09-2016-estimated-cost-at-list-prices](adr/2026-11-09-2016-estimated-cost-at-list-prices.md) — dollar estimate per response from a local price table
- [2026-11-09-2050-subagents-in-session-project](adr/2026-11-09-2050-subagents-in-session-project.md) — a subagent's project is its session's
- [2026-11-10-2227-turns-partition-the-work](adr/2026-11-10-2227-turns-partition-the-work.md) — every turn, not just human prompts, partitions the work; subagents belong to their spawning turn
- [2026-11-11-1102-usage-by-account-and-weekly-quota](adr/2026-11-11-1102-usage-by-account-and-weekly-quota.md) — usage attributed to subscriptions by account UUID or host/profile; quota use in list-price dollars per weekly window

## Changes

- [2026-11-11-1104-quota-on-dashboard](changelog/2026-11-11-1104-quota-on-dashboard.md) — Weekly quota card on Overview; Account filter
- [2026-11-11-1102-accounts-and-quota-api](changelog/2026-11-11-1102-accounts-and-quota-api.md) — `account` dimension and `/api/quota`
- [2026-11-10-2240-turns-tab](changelog/2026-11-10-2240-turns-tab.md) — Turns tab: who starts the work, when, longest turns, timer check; cost by effort and skill
- [2026-11-10-2227-turns-in-data-and-api](changelog/2026-11-10-2227-turns-in-data-and-api.md) — turns with origins, effort and skill; turns, heatmap, timecheck and cost endpoints
- [2026-11-10-0051-api-tab](changelog/2026-11-10-0051-api-tab.md) — API reference tab in the dashboard
- [2026-11-10-0051-query-client](changelog/2026-11-10-0051-query-client.md) — `ccstats-query`, a Python client for the API
- [2026-11-09-2055-hourly-push-schedule](changelog/2026-11-09-2055-hourly-push-schedule.md) — push timers run hourly
- [2026-11-09-2047-hourly-hub-schedule](changelog/2026-11-09-2047-hourly-hub-schedule.md) — hub collect, pulls and refresh run hourly
- [2026-11-09-2043-readme-stats-hook](changelog/2026-11-09-2043-readme-stats-hook.md) — README stats table refreshed by a pre-commit hook
- [2026-11-09-2050-subagents-in-session-project](changelog/2026-11-09-2050-subagents-in-session-project.md) — worktree agents folded into their session's project
- [2026-11-09-2035-dashboard-tabs](changelog/2026-11-09-2035-dashboard-tabs.md) — tabbed dashboard, filters in the URL, hosts and collection status
- [2026-11-09-2016-estimated-cost](changelog/2026-11-09-2016-estimated-cost.md) — dollar estimates in API, dashboard and CLI
- [2026-11-09-1945-session-rollups-agent-tree-timeline](changelog/2026-11-09-1945-session-rollups-agent-tree-timeline.md) — cached rollups, subagent hierarchy, session timeline
- [2026-11-07-0159-hub-archive-api-dashboard](changelog/2026-11-07-0159-hub-archive-api-dashboard.md) — first working hub
