# Estimated dollar cost

- `pricing.py`: API list prices per model, including cache writes by TTL and Haiku 5.5's long-prompt tier; every stored response gets a `usd` estimate, summed per thread and session, and re-priced without re-parsing when the table changes
- API: `/api/summary` rows carry `cost` (`main`, `sub`, `total`), `/api/models` adds `usd` and `priced`, sessions and threads have `usd`, and `/api/sessions` takes `sort=cost`
- Dashboard: estimated-cost tile, a cost-per-bucket chart (main vs subagents) with a per-prompt table, cost by model, and Cost columns in the tokens, host, spawner and session tables; the session view adds a cost tile next to Claude Code's own figure, and cost per thread in the tree and timeline
- CLI: `report` and `sessions` show a cost column
- Schema v3 rebuilds the database from the archive on first start
