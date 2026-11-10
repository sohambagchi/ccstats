# Every script and command reports what it does

- `ccstats collect`, `pull` and `compute` print each source before and after (files updated, size, bytes transferred, seconds), the updated and parsed files, and ssh's own error on failure; output is line-buffered for the journal; global `-q` keeps summaries and errors
- `ccstats serve` logs one line per request, each refused client, and each refresh with its trigger (startup, timer, `POST from <ip>`)
- `push.sh`: `install` and `uninstall` list each step; `run` shows each profile, rsync's file list and byte summary (per-file progress on a terminal), the refresh result and elapsed time; `run -q` for errors and a summary
- `cloudlab-setup.sh` reports project, node, copies, symlinks and the retention change, and ends with the hub's `[[pull]]` block; `-q` for errors only
- `deploy/install-hub.sh` shows the build log, each file written, the first collect's output, the timer and the dashboard URL; `-q` for errors and the summary
- Collect no longer counts rsync's stats lines as updated files when nothing changed
