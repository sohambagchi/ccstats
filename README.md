# ccstats

Claude Code usage across machines: sessions, subagents, tokens, tool calls and Claude time,
per day and week, served as a dashboard and JSON API on the tailnet. Design and rationale:
[docs/INDEX.md](docs/INDEX.md).

## What runs where

| Where | What | How often |
|---|---|---|
| hub (spore) | `ccstats-serve.service`: API + dashboard on `:8420`; re-parses changed files every 5 min and on push | always on |
| hub | `ccstats-collect.timer`: archive spore's own transcripts, rsync CloudLab `/proj` trees, compute | every 15 min |
| push host (laptop, desktop) | `ccstats-push.timer` (systemd; launchd/cron fallback): rsync transcripts to the hub, POST `/api/refresh` | every 15 min |
| CloudLab node | nothing; `cloudlab-setup.sh` once puts transcripts on `/proj` | — |

## Hub

```sh
deploy/install-hub.sh            # builds, links ~/.local/bin/ccstats, writes config.toml, enables units
ccstats report --days 7 --tools  # terminal tables
ccstats sessions --days 3
ccstats check                    # token sums vs Claude Code's cost-state
```

Data: `~/.local/share/ccstats/{archive/,ccstats.db}`. Delete `ccstats.db` (or run
`ccstats compute --full`) to rebuild from the archive.

## Push host

```sh
curl -fsS http://spore:8420/push.sh | sh -s -- install     # prints a `ccstats host add ...` line
```

Run the printed line on the hub, then `ccstats-push run` on the host. The key can only write
into `archive/<host>/` on the hub. `ccstats-push status`, `ccstats-push uninstall`.

## CloudLab

On each node, before starting Claude Code there. From the hub (nodes may not reach spore):

```sh
ccstats script cloudlab-setup | ssh <you>@<node> sh -s -- <project>
```

or, in the profile's startup script on a node that can reach the hub,
`curl -fsS http://spore:8420/cloudlab-setup.sh | sh -s -- <project>`.

and on the hub, in `~/.config/ccstats/config.toml`:

```toml
[[pull]]
name = "utah"
kind = "cloudlab"
ssh = ["<you>@<any live node>"]
path = "/proj/<project>/ccstats/"
```

## API

`GET /api` lists endpoints. Stats endpoints take `days=N` or `since`/`until` (local
`YYYY-MM-DD`), and `host`, `profile`, `project`, `model`.

```sh
curl 'http://spore:8420/api/summary?group=week&by=host'
curl 'http://spore:8420/api/tools?days=7&top=10'
curl 'http://spore:8420/api/sessions?days=1'
curl 'http://spore:8420/api/sessions?days=30&sort=subagents&limit=5'
curl 'http://spore:8420/api/models?days=30'                  # tokens and estimated $ per model
curl 'http://spore:8420/api/session?host=spore&profile=claude&id=<session-id>'   # agent tree + timeline spans
```

Per-session and per-thread numbers are computed when a transcript changes, not per request; answers
are cached until the next change. In the dashboard, click a session for its agent tree and timeline.

Dollar figures are estimates at API list prices (`src/ccstats/pricing.py`), not what a subscription bills.

Clients outside loopback, the tailnet, private ranges and spore's attached subnets get 403.

## Development

```sh
nix develop -c pytest -q
nix build && ./result/bin/ccstats --help
```
