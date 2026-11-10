# Hub runs hourly

- `ccstats-collect.timer` (archive local transcripts, CloudLab ssh pulls, compute) moves from every 15 min to `OnCalendar=hourly`
- `ccstats-serve`'s timed database refresh moves from 5 min to 1 h; a push's `POST /api/refresh` still refreshes immediately
- The dashboard reloads open pages hourly instead of every 5 min
- Push timers are unchanged (every 15 min); `push.sh`'s refresh message now says the hub picks files up within the hour
