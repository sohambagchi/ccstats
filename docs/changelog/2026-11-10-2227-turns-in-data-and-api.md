# Turns, effort and skill in the data and the API

- Every turn is now a record with its origin (you, a task notification, another session, a schedule, ...); Claude time, responses and tool calls each belong to one, and subagent work to the turn that spawned it.
- New endpoints: `/api/turns` (longest turns with the subagents, tools and cost each led to), `/api/heatmap` (time by weekday and hour), `/api/timecheck` (Claude time against Claude Code's turn timer), `/api/cost?by=model|effort|skill|origin`; `/api/summary` takes `by=origin` and rows carry `turns`; `/api/session` returns the session's turns.
- On the real archive: median Claude time is 0.97 of Claude Code's timer per turn, 1,094 of 1,553 timed turns within 10%.
- `ccstats check` also reports the timer comparison; `ccstats report --by origin`; `ccstats-query` gains `cost`, `turns`, `heatmap`, `timecheck`.
- API reference updated in the dashboard tab and [2026-11-10-0051-http-api-reference](../analysis-artifacts/2026-11-10-0051-http-api-reference.md).
- Rationale: see ADR [2026-11-10-2227-turns-partition-the-work](../adr/2026-11-10-2227-turns-partition-the-work.md)
