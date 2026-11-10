# HTTP API reference

How to query a running ccstats server. Mirrors the dashboard's API tab
(`src/ccstats/static/index.html`, `#api`); change both together. Examples use the hub,
`http://spore:8420`.

## Quick start

Every number on the dashboard comes from a JSON API on the same server. There is no token:
reads are open to loopback, the tailnet (100.64.0.0/10), private ranges (10/8, 172.16/12,
192.168/16, fc00::/7) and the server's own subnets, plus any `allow` networks in its config.
Everyone else gets an empty 403.

```sh
curl -s 'http://spore:8420/api/summary?days=7&group=total'
curl -s 'http://spore:8420/api/sessions?days=3&sort=cost&limit=5'
curl -s 'http://spore:8420/api'
```

`GET /api` lists the endpoints in machine-readable form. Day boundaries are local to the
server's time zone (`timezone` in `/api/health`). Answers carry
`Access-Control-Allow-Origin: *`, so a page on another origin can `fetch()` them.

## Filters

Every stats endpoint (summary, models, tools, sessions) takes the same filters. All are
optional; without a date filter the whole archive is covered.

| Parameter | Value | Meaning |
|---|---|---|
| `days` | N | The last N local days, today included. `days=1` is today. Ignored if `since` is set. |
| `since` | YYYY-MM-DD | First local day, inclusive. |
| `until` | YYYY-MM-DD | Last local day, inclusive. |
| `host` | name | Machine the transcripts came from, e.g. `spore`. |
| `profile` | name | Claude Code config dir on that host, e.g. `claude` for `~/.claude`. |
| `project` | name | Project (working directory name). Subagents count toward their session's project. |
| `model` | model id | Restricts tokens, cost and tool calls to responses from that model. Prompts, time and session counts are not split by model. |

Valid values for `host`, `profile`, `project` and `model` come from `/api/dimensions`. A
malformed date is a 400.

## Endpoints

### GET /api/summary

Stats per time bucket, optionally split by one dimension.

- `group` = `day` (default), `week` (ISO week, `2026-W45`), `month` (`2026-11`) or `total`
- `by` = `host`, `profile`, `project` or `model`; each row then has that key. With `by=model` only token, cost and tool fields are filled.
- Plus the filters.

```sh
curl -s 'http://spore:8420/api/summary?group=week&by=host&days=28'
```

Answer: `{"group", "by", "filter", "rows": [...]}`, rows sorted by bucket. Each row:

| Field | Meaning |
|---|---|
| `bucket` | Day, week, month or `"total"`. |
| `sessions` | Main sessions with a response or prompt in the bucket. |
| `prompts` | Human prompts. |
| `subagents`, `sessions_with_subagents`, `agent_types` | Subagents started, sessions that started any, and a count per agent type. |
| `max_spawn_depth`, `mean_spawn_depth`, `peak_concurrent_subagents` | How deep subagents nested and how many ran at once. |
| `claude_s`, `api_s` | Claude time and its API-only part, main threads, in seconds. |
| `agent_s`, `agent_api_s` | The same for subagents, summed across parallel agents. |
| `api_calls_main`, `api_calls_sub` | Model responses. |
| `tokens.main`, `tokens.sub` | `input`, `output`, `thinking`, `cache_read`, `cache_write`, `total`, `total_no_cache_read`. |
| `cost` | `{"main", "sub", "total"}`, estimated USD. |
| `tool_calls`, `tool_calls_sub`, `tool_errors`, `tool_background` | All tool calls, those made by subagents, those that errored, those run in the background. |

### GET /api/models

Tokens and estimated cost per model, most expensive first. Filters apply.

```sh
curl -s 'http://spore:8420/api/models?days=30'
```

Rows: `model`, `api_calls`, `input`, `output`, `thinking`, `cache_read`, `cache_write`, `usd`,
`priced` (false when the model isn't in the price table, so `usd` is 0).

### GET /api/tools

Tool calls by tool name, and Bash commands by the program they run. `top` = rows per list
(default 25). Filters apply.

```sh
curl -s 'http://spore:8420/api/tools?days=7&top=10'
```

Answer: `{"by_name": [{name, calls, errors, background, in_subagents, seconds}], "bash": [{exe, calls, errors, background}]}`.
`seconds` is wall time from call to result.

### GET /api/sessions

Main sessions active in the range, each with its subagents folded in.

- `sort` = `recent` (default), `cost`, `claude`, `agent`, `subagents` or `output`
- `limit` = rows, default 100, at most 1000
- Plus the filters, except `model`. A session is in range if any of it falls in range.

```sh
curl -s 'http://spore:8420/api/sessions?days=7&sort=subagents&limit=5'
```

Rows: `host`, `profile`, `session_id`, `project`, `cwd`, `title`, `model`, `cc_version`,
`branch`, `first_ts`, `last_ts`, `first_day`, `last_day`, `prompts`, `compactions`,
`subagents`, `agent_types`, `depth_counts`, `forks`, `background_agents`, `max_spawn_depth`,
`peak_concurrent_subagents`, `api_calls`, `tool_calls`, `tool_errors`, `claude_s`, `api_s`,
`agent_s`, `usd`, `tokens` (`input`, `output`, `thinking`, `cache_read`, `cache_write`),
`path` (in the archive). `cost_usd` is Claude Code's own figure when it recorded one, else null.

### GET /api/session

One session in full: its row as above, every thread (main and subagents) with stats and busy
spans, and its prompts. `host`, `profile` and `id` (the `session_id`) are required; an unknown
session is a 404.

```sh
curl -s 'http://spore:8420/api/session?host=spore&profile=claude&id=SESSION_ID'
```

Answer: `{"session", "threads", "prompts"}`. Each thread has `id` (`"main"` or the agent id),
`parent` (null for main), `is_subagent`, `agent_type`, `description`, `spawn_depth`,
`is_fork`, `background`, `model`, `first_ts`, `last_ts`, `spans` (`[[start, end], ...]` when
it was busy), and `api_calls`, `input`, `output`, `thinking`, `cache_read`, `cache_write`,
`tool_calls`, `tool_errors`, `agent_calls`, `claude_s`, `api_s`, `usd`. Build the tree by
following `parent`. `prompts` is `[{ts, claude_s}]`: each human prompt and the Claude time it
led to.

### GET /api/dimensions

Values for the filters: `{"host": [...], "profile": [...], "project": [...], "model": [...], "first_day", "last_day"}`.
No parameters.

```sh
curl -s 'http://spore:8420/api/dimensions'
```

### GET /api/hosts

Where data comes from, all time, unfiltered and never cached:

- `threads`: per host and profile, `sessions`, `subagents`, `first_ts`, `last_ts`
- `archive`: `"host/profile"` → time the newest file arrived
- `collect`: per source the hub collects (`"host/profile"` or `"pull:name"`), `last_attempt`, `last_success`, `error`, `via`
- `push_hosts`: hosts registered to push to the hub

```sh
curl -s 'http://spore:8420/api/hosts'
```

### GET /api/health

`{"ok", "version", "timezone", "computed_at", "last_refresh"}`; `last_refresh` has the counts
of files `parsed`, `unchanged` and `removed` and when (`at`). Answers even before the database
exists.

```sh
curl -s 'http://spore:8420/api/health'
```

### POST /api/refresh

Re-parses transcripts that changed since the last refresh, in the background. Answers
`202 {"queued": true}` at once; new numbers appear a few seconds later. Safe to call any time;
the server also refreshes hourly.

```sh
curl -s -X POST 'http://spore:8420/api/refresh'
```

## Units and definitions

- Fields ending in `_s` and `seconds` are seconds. `*_ts` and span ends are Unix epoch seconds. `*_day` and buckets are local to the server's time zone.
- **Claude time** (`claude_s`) is the union of model-response and tool-run intervals in main threads, minus waits for a human; a tool call a human had to approve counts for at most 10 minutes (the hub's `approval_cap_minutes`). **Agent time** (`agent_s`) is the same per subagent, summed, so it can exceed wall time when agents run in parallel. See [2026-11-07-0159-claude-time-definition](../adr/2026-11-07-0159-claude-time-definition.md).
- `usd` and `cost` are estimates: tokens priced at API list rates, not what a subscription bills. See [2026-11-09-2016-estimated-cost-at-list-prices](../adr/2026-11-09-2016-estimated-cost-at-list-prices.md).
- Token counts are as the API reported them; `thinking` is part of `output`.

## Caching and errors

- Stats answers carry an `ETag` that changes when new transcripts are parsed or the local day rolls over. Send it back as `If-None-Match` to get an empty `304`. Repeated identical requests are served from memory.
- Errors are JSON `{"error": "..."}`: 400 bad parameter, 404 unknown path or session, 503 when 16 requests are already in flight or the database hasn't been built yet, 500 for a server bug. A 403 (address not allowed) has an empty body.

## Python client

`ccstats-query.py` (source: `src/ccstats/remote/ccstats-query.py`) is a single file, Python
3.8+ with no dependencies, that prints these answers as tables or JSON. Downloaded from a
server, it already points at that server:

```sh
curl -fsS http://spore:8420/ccstats-query.py -o ~/.local/bin/ccstats-query && chmod +x ~/.local/bin/ccstats-query
```

On the hub, `ccstats script query` prints the same file. Usage:

```sh
ccstats-query summary --days 7                      # one row per day
ccstats-query summary --days 28 --group week --by host
ccstats-query sessions --days 3 --sort cost --limit 10
ccstats-query session spore claude <session-id>     # agent tree
ccstats-query models --days 30 --project ccstats
ccstats-query tools --days 7 --top 10
ccstats-query hosts                                 # JSON
ccstats-query refresh
ccstats-query --json summary --group total --days 30 | jq '.rows[0].cost.total'
ccstats-query get /api/summary group=month by=model # any endpoint, raw JSON
```

The server is the first of: `--server` / `-s`, `$CCSTATS_SERVER`, `server = "..."` in
`~/.config/ccstats/client.toml`, the server it was downloaded from, `localhost:8420`. It can be
`host`, `host:port` or a URL; a bare host means port 8420 over http, so give a full URL
(`https://stats.example.com`) for a server behind a reverse proxy.

```sh
ccstats-query -s spore:8420 health
CCSTATS_SERVER=100.101.102.103 ccstats-query sessions
echo 'server = "spore:8420"' > ~/.config/ccstats/client.toml
```

It is also a module:

```python
import os, runpy
Client = runpy.run_path(os.path.expanduser("~/.local/bin/ccstats-query"))["Client"]
week = Client("spore:8420").get("/api/summary", days=7, group="total")["rows"][0]
print(week["claude_s"] / 3600, week["cost"]["total"])
```
