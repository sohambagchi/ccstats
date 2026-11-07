# Hub serves a stdlib HTTP API and dashboard; reads open to tailnet and local networks

**Status:** accepted

**Context:** Stats must be queryable from other tailnet/LAN machines. The plan's static `report.html` plus a daily headless-Claude job republishing a claude.ai artifact was the earlier output.
**Decision:** `ccstats serve` (stdlib `ThreadingHTTPServer`, default `0.0.0.0:8420`) serves `/` and a JSON API under `/api/`. Requests are accepted only from loopback, Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48), RFC 1918/ULA, the subnets of this machine's interfaces, and `allow` in config; others get 403. No tokens. At most 16 requests run at once, each with a 30 s socket timeout. The claude.ai artifact job is dropped.
**Alternatives:** FastAPI/uvicorn — a dependency for no gain at this data size; token for reads — the user chose open reads.
**Consequences:** on spore, `eno1` is a public /24 (155.98.68.0/24), so that whole subnet can read the stats. `/api/refresh` (POST) is unauthenticated but idempotent and coalesced.
