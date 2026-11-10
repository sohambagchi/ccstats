# Push hosts run hourly

- `push.sh install` now schedules `ccstats-push run` hourly: systemd `OnCalendar=hourly`, launchd `StartInterval` 3600, cron `0 * * * *` (was every 15 min)
- Existing hosts pick it up by re-running `curl -fsS http://spore:8420/push.sh | sh -s -- install`; the key and `push.env` are reused, so no hub re-registration
