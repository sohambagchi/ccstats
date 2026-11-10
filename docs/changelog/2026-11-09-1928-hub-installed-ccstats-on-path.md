# Hub installed on spore; `ccstats` on PATH

- `ccstats-serve.service` and `ccstats-collect.timer` run on spore; lingering is on
- `deploy/install-hub.sh` links `~/.local/bin/ccstats` to the pinned build, so `ccstats host add` and the other CLI commands work from a shell and follow upgrades
