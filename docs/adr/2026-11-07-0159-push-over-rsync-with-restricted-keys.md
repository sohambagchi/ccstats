# Push hosts send transcripts with rsync over ssh, one restricted key each

**Status:** accepted

**Context:** No personal host accepts ssh from the hub, and laptops sleep or sit behind NAT, so the hub can't pull from them.
**Decision:** Each push host runs `ccstats-push run` (POSIX sh, served by the hub at `/push.sh`) from a 15-minute systemd/launchd/cron timer. It rsyncs to the hub with a dedicated key; `ccstats host add NAME KEY` puts it in `~/.ssh/authorized_keys` as `command="rrsync -wo -no-del archive/NAME",restrict`. After a push it POSTs `/api/refresh`.
**Alternatives:** HTTP upload to the hub with per-host tokens — needs no ssh setup and works on Windows, but the user chose rsync.
**Consequences:** a key can only add files under its own `archive/NAME/` (no shell, reads or deletes). Push hosts need rsync, ssh and sh; Windows (`gram`) needs WSL. ssh pulls remain for hosts the hub can reach (`[[pull]] kind = "host"`).
