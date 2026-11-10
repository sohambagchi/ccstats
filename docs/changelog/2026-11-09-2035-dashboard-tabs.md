# Dashboard redesigned around tabs

- One sticky top bar: tabs (Overview, Cost & tokens, Tools, Agents, Sessions, Hosts) and one row of filters; range and bucket are segmented buttons, project is a type-ahead, and the API's `model` filter is now exposed
- Filters live in the query string and the tab in the hash (`/?days=7&host=yeast#tools`), so views can be linked and survive a reload; each tab fetches only its own data, cached in the page until the next refresh
- Overview: headline tiles that link to their tab, one activity chart switchable between Claude time, tokens and cost, recent sessions, and hosts (click a host to filter by it)
- Cost & tokens adds cost by project (click to filter); Tools adds error rates and time running; tables sort by clicking a column header
- Sessions: search over title, project, host and id, server-side sort (recent, cost, Claude time, agent time, subagents, output), "show more" up to 1000
- Hosts: per-host stats plus collection status from `/api/hosts` (archive freshness per host/profile, pull sources, push hosts), which the dashboard didn't show before
- Session view hides the filters and links back to the tab it was opened from; keys `1`–`6` switch tabs, `/` searches sessions, `Esc` leaves a session
