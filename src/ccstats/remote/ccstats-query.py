#!/usr/bin/env python3
"""Query a running ccstats server from any machine. Python 3.8+, standard library only.

    ccstats-query.py summary --days 7 --by host
    ccstats-query.py sessions --days 3 --sort cost --limit 10
    ccstats-query.py session spore claude <session-id> --json
    ccstats-query.py get /api/tools top=5 days=1        # any endpoint, raw JSON

The server is the first of: --server, $CCSTATS_SERVER, `server = "..."` in
~/.config/ccstats/client.toml, the hub this copy was downloaded from, localhost:8420.
It may be `host`, `host:port` or a URL; a bare host means port 8420 over http, so give a
URL (`https://stats.example.com`) for a server behind a reverse proxy.

Also usable from Python, whatever the file is named:
    Client = runpy.run_path("ccstats-query.py")["Client"]
    Client("spore:8420").get("/api/summary", days=7, group="total")
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PORT = 8420
DOWNLOADED_FROM = "@HUB_URL@"  # filled in when the hub serves this file
CONFIG = os.path.expanduser("~/.config/ccstats/client.toml")


def base_url(server):
    """`spore`, `spore:8420`, `[::1]:8420` or `https://x.example` -> URL without trailing slash."""
    server = server.strip().rstrip("/")
    if "://" in server:
        return server
    if server.count(":") > 1 and not server.startswith("["):  # bare IPv6 address
        server = f"[{server}]"
    return f"http://{server}" if re.search(r":\d+$", server) else f"http://{server}:{DEFAULT_PORT}"


def configured_server():
    if os.environ.get("CCSTATS_SERVER"):
        return os.environ["CCSTATS_SERVER"]
    try:
        with open(CONFIG) as f:
            m = re.search(r'^\s*server\s*=\s*"([^"]+)"', f.read(), re.M)
            if m:
                return m.group(1)
    except OSError:
        pass
    if "@" not in DOWNLOADED_FROM:
        return DOWNLOADED_FROM
    return f"localhost:{DEFAULT_PORT}"


class ApiError(Exception):
    pass


class Client:
    def __init__(self, server=None, timeout=30):
        self.base = base_url(server or configured_server())
        self.timeout = timeout

    def get(self, path, **params):
        """GET an endpoint; None-valued parameters are left out. Returns the decoded JSON."""
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        return self._call(urllib.request.Request(self.base + path + ("?" + q if q else "")))

    def refresh(self):
        return self._call(urllib.request.Request(self.base + "/api/refresh", data=b"", method="POST"))

    def _call(self, req):
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise ApiError(f"{self.base}: 403, this address is not allowed (see the server's allow list)")
            try:
                msg = json.loads(e.read()).get("error")
            except ValueError:
                msg = None
            raise ApiError(f"{req.full_url}: {e.code} {msg or e.reason}")
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(f"{self.base}: {getattr(e, 'reason', e)}")


# ---- Output --------------------------------------------------------------------------

def dur(s):
    s = round(s or 0)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m"


def num(n):
    n = n or 0
    for at, div, unit in ((1e9, 1e9, "B"), (1e6, 1e6, "M"), (1e4, 1e3, "K")):
        if abs(n) >= at:
            return f"{n / div:.1f}{unit}"
    return f"{n:,.0f}"


def usd(x):
    return f"${x or 0:,.2f}"


def table(head, rows, left=1):
    """Print aligned columns: the first `left` are text, the rest numbers."""
    rows = [[str(c) for c in r] for r in rows]
    w = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h)) for i, h in enumerate(head)]
    line = lambda cells: "  ".join(c.ljust(w[i]) if i < left else c.rjust(w[i]) for i, c in enumerate(cells))  # noqa: E731
    print(line([str(h) for h in head]))
    for r in rows:
        print(line(r))
    if not rows:
        print("(no rows)")


def show_summary(d):
    by = d.get("by")
    head = ["bucket"] + ([by] if by else []) + ["sessions", "prompts", "subagents", "claude", "agent", "tools", "output", "cost"]
    table(head, [[r["bucket"]] + ([r.get(by) or "-"] if by else []) + [
        r["sessions"], r["prompts"], r["subagents"], dur(r["claude_s"]), dur(r["agent_s"]), num(r["tool_calls"]),
        num(r["tokens"]["main"]["output"] + r["tokens"]["sub"]["output"]), usd(r["cost"]["total"])] for r in d["rows"]],
          left=2 if by else 1)


def show_sessions(rows):
    table(["id", "last day", "host/profile", "project", "title", "claude", "agent", "subagents", "cost"],
          [[r["session_id"], r["last_day"], f"{r['host']}/{r['profile']}", r["project"] or "-", (r["title"] or "")[:40],
            dur(r["claude_s"]), dur(r["agent_s"]), r["subagents"], usd(r["usd"])] for r in rows], left=5)


def show_models(rows):
    table(["model", "api calls", "input", "output", "cache read", "cache write", "cost"],
          [[r["model"], num(r["api_calls"]), num(r["input"]), num(r["output"]), num(r["cache_read"]),
            num(r["cache_write"]), usd(r["usd"]) + ("" if r["priced"] else " ?")] for r in rows])


def show_tools(d):
    table(["tool", "calls", "errors", "background", "in subagents", "time"],
          [[r["name"], r["calls"], r["errors"], r["background"], r["in_subagents"], dur(r["seconds"])] for r in d["by_name"]])
    print()
    table(["bash program", "calls", "errors", "background"], [[r["exe"], r["calls"], r["errors"], r["background"]] for r in d["bash"]])


def show_session(d):
    s = d["session"]
    print(f"{s['title'] or s['session_id']}\n{s['host']}/{s['profile']}  {s['project']}  {s['cwd']}  {s['session_id']}")
    print(f"claude {dur(s['claude_s'])}, agent {dur(s['agent_s'])}, {s['subagents']} subagents, {usd(s['usd'])}\n")
    kids = {}
    for t in d["threads"]:
        kids.setdefault(t["parent"], []).append(t)
    rows = []

    def walk(t, depth):
        name = "main" if not t["is_subagent"] else f"{t['agent_type'] or '?'}: {t['description'] or t['id']}"
        rows.append(["  " * depth + name[:60], dur(t["claude_s"]), num(t["tool_calls"]), num(t["output"]), usd(t["usd"])])
        for c in kids.get(t["id"], []):
            walk(c, depth + 1)
    for t in kids.get(None, []):
        walk(t, 0)
    table(["thread", "busy", "tools", "output", "cost"], rows)


# ---- CLI -----------------------------------------------------------------------------

def main(argv=None):
    filters = argparse.ArgumentParser(add_help=False)
    g = filters.add_argument_group("filters")
    g.add_argument("--days", type=int, help="last N local days, today included")
    g.add_argument("--since", help="first day, YYYY-MM-DD")
    g.add_argument("--until", help="last day, YYYY-MM-DD")
    for k in ("host", "profile", "project", "model"):
        g.add_argument("--" + k)

    p = argparse.ArgumentParser(prog="ccstats-query", description="Query a ccstats server's HTTP API.")
    p.add_argument("--server", "-s", help=f"host[:port] or URL (default: {configured_server()})")
    p.add_argument("--json", action="store_true", help="print the raw JSON answer")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health", help="server version and last refresh")
    sub.add_parser("endpoints", help="what the server says it serves")
    s = sub.add_parser("summary", parents=[filters], help="stats per day/week/month or in total")
    s.add_argument("--group", choices=["day", "week", "month", "total"], default="day")
    s.add_argument("--by", choices=["host", "profile", "project", "model"])
    sub.add_parser("models", parents=[filters], help="tokens and estimated cost per model")
    t = sub.add_parser("tools", parents=[filters], help="tool calls by name and Bash program")
    t.add_argument("--top", type=int, default=25)
    ss = sub.add_parser("sessions", parents=[filters], help="sessions with their subagents folded in")
    ss.add_argument("--limit", type=int, default=20)
    ss.add_argument("--sort", choices=["recent", "subagents", "claude", "agent", "output", "cost"], default="recent")
    one = sub.add_parser("session", help="one session's agent tree and per-thread stats")
    one.add_argument("host")
    one.add_argument("profile")
    one.add_argument("id")
    sub.add_parser("dimensions", help="known hosts, profiles, projects, models and day range")
    sub.add_parser("hosts", help="archive coverage and collection status")
    sub.add_parser("refresh", help="ask the server to re-parse changed transcripts now")
    raw = sub.add_parser("get", help="GET any path; parameters as key=value")
    raw.add_argument("path")
    raw.add_argument("params", nargs="*", metavar="key=value")
    a = p.parse_args(argv)

    c = Client(a.server)
    f = {k: getattr(a, k, None) for k in ("days", "since", "until", "host", "profile", "project", "model")}
    try:
        if a.cmd == "refresh":
            out, show = c.refresh(), None
        elif a.cmd == "get":
            path = a.path if a.path.startswith("/") else "/" + a.path
            out, show = c.get(path, **dict(kv.split("=", 1) for kv in a.params)), None
        elif a.cmd == "endpoints":
            out, show = c.get("/api"), None
        elif a.cmd == "summary":
            out, show = c.get("/api/summary", group=a.group, by=a.by, **f), show_summary
        elif a.cmd == "tools":
            out, show = c.get("/api/tools", top=a.top, **f), show_tools
        elif a.cmd == "models":
            out, show = c.get("/api/models", **f), show_models
        elif a.cmd == "sessions":
            out, show = c.get("/api/sessions", limit=a.limit, sort=a.sort, **f), show_sessions
        elif a.cmd == "session":
            out, show = c.get("/api/session", host=a.host, profile=a.profile, id=a.id), show_session
        else:
            out, show = c.get("/api/" + a.cmd), None
    except ApiError as e:
        sys.exit(f"ccstats-query: {e}")
    if a.json or show is None:
        print(json.dumps(out, indent=2))
    else:
        show(out)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
