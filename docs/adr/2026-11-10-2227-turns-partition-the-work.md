# Turns, not human prompts, partition the work; subagents belong to the turn that spawned them

**Status:** accepted

**Context:** Claude time was cut only at human prompts, so work woken by a task notification, a message from another session or a scheduled wakeup was credited to whatever prompt came before it. Who starts the work, which turns run longest, and how Claude time compares with Claude Code's own `turn_duration` all need a per-turn unit.
**Decision:**
- A turn starts at every main-thread user record carrying `origin.kind` (or, without it, `turnOrigin`): `human`, `task-notification`, `peer`, `scheduled`, `auto-continuation`, `coordinator`. Older transcripts fall back to plain text = `human`, `<task-notification…>` = `task-notification`.
- Busy time is cut at every turn start (and at local hour marks, for the heatmap); each response and tool call records its turn. `prompts` stays the count of `human` turns.
- A subagent's records belong to its root turn: the main-thread turn whose `Agent` call spawned it, or its parent's root turn when nested. Rollup resolves this into `threads.root_turn` and per-turn totals into `turn_stats`.
- `system/turn_duration` is kept per turn as `cc_turn_s` and used only as a check on Claude time, never as Claude time (see [2026-11-07-0159-claude-time-definition](2026-11-07-0159-claude-time-definition.md)).
**Alternatives:** keep cutting at human prompts and tag autonomous turns separately — their time would still land on the wrong prompt; credit subagent work to the turn active when it ran — a long background agent would be split across later, unrelated turns.
**Consequences:** per-prompt Claude time from before this change is now split across the prompt and the autonomous turns after it; totals are unchanged. Subagents whose spawning call is missing (e.g. compacted away) count as origin `unknown`. Parsing and the schema changed, so the database is rebuilt once (about 20 s).
