# Per-session rollups, agent tree and timeline

- Stats per thread and per session are computed at refresh, only for sessions whose transcripts changed; page loads read them (`/api/sessions` 13 s → 1 ms) and the server caches every answer until the data changes (ETag, 304)
- Subagents are linked to the thread that spawned them (main or another subagent); sessions record spawned count, types, per-depth counts, forks, background agents, peak concurrency
- `GET /api/session?host=&profile=&id=`: agent tree with per-thread busy time, tools, tokens and activity spans, plus prompt times; `/api/sessions` takes `sort=recent|subagents|claude|agent|output`
- Dashboard: subagents by type, sessions that spawned the most, clickable sessions opening a session view (`#session/<host>/<profile>/<id>`): timeline with one lane per thread in tree order, idle stretches over 10 min squeezed, prompt marks; and an indented agent-tree table
- Parser v2 stores each thread's busy spans and fork/background flags; schema v2 rebuilds the database from the archive on first start
