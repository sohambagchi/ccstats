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
deploy/install-hub.sh            # builds, writes ~/.config/ccstats/config.toml, enables units
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

On each node (or in the profile's startup script), before starting Claude Code:

```sh
curl -fsS http://spore:8420/cloudlab-setup.sh | sh -s -- <project>
```

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
```

Clients outside loopback, the tailnet, private ranges and spore's attached subnets get 403.

## Development

```sh
nix develop -c pytest -q
nix build && ./result/bin/ccstats --help
```
