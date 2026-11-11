# Usage by account, and weekly quota use, in the data and the API

- New dimension `account` (the subscription): filter `account=` and `by=account` on every stats endpoint, `--account` and `--by account` in `ccstats report` and `ccstats-query`; sessions rows carry `account`.
- Config `[accounts.<name>]` takes `ids` (account UUIDs), `profiles` (`host/profile` dirs logged in to it), `weekly_reset` and `weekly_quota_usd`. The hub's config maps spore and buckwheat `~/.claude` to `claudia`.
- New endpoint `/api/quota`: per account, the cost in each weekly-limit window and its share of the weekly quota, with means over full windows; `ccstats-query quota`.
- The stored account UUID column is now `threads.account_id`; the database is rebuilt once.
- On the real archive: `claude` averages 61% of its estimated quota over the last four full windows (39%, 83%, 81%, and 100% in the window that hit the limit); `claudia` averages 27%.
- API reference updated in the dashboard tab and [2026-11-10-0051-http-api-reference](../analysis-artifacts/2026-11-10-0051-http-api-reference.md).
- Rationale: see ADR [2026-11-11-1102-usage-by-account-and-weekly-quota](../adr/2026-11-11-1102-usage-by-account-and-weekly-quota.md)
