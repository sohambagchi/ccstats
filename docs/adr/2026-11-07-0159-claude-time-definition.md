# Claude time is the union of API and tool intervals, minus human waits

**Status:** accepted

**Context:** `system/turn_duration` is sparse and includes hours of background waiting, so it can't measure how long Claude worked.
**Decision:** Per thread, Claude time = union of
- API intervals: `api-request.timestamp` (matched by the assistant's `requestRef`) → last assistant record of that message; transcripts without `api-request` records (CC < ~2.1.31x) start at the record before the response;
- tool intervals: `tool_use` → its `tool_result`, except `AskUserQuestion`/`ExitPlanMode` (human decisions); an interval whose `permissionDecision.source` is `user_*`, or (without that field) whose permission mode prompts, is capped at `approval_cap_minutes` (10).

Time is cut at human prompts (credited to the prompt it follows) and at local midnights. API time is the API-only union; agent time is the same union per subagent, summed.
**Consequences:** idle gaps between turns and background waits drop out by construction; work woken by a task notification counts. Approximation is accepted: guessed request starts and the approval cap can misstate individual turns by seconds to minutes.
