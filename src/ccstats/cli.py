"""ccstats command line."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from importlib import resources
from zoneinfo import ZoneInfo

from . import __version__, collect, config, hosts, metrics, store


def _compute(cfg, full=False, quiet=False):
    if not quiet:
        print(f"compute: {cfg.archive} -> {cfg.db}" + (" (full re-parse)" if full else ""))
    with store.compute_lock(cfg.data_dir):
        conn = store.connect(cfg.db)
        try:
            stats = store.refresh(conn, cfg.archive, cfg.settings, full=full,
                                  log=None if quiet else (lambda m: print(f"  {m}")))
        finally:
            conn.close()
    print(f"compute: {stats['parsed']} parsed, {stats['unchanged']} unchanged, {stats['removed']} removed, {stats['sessions']} sessions rolled up in {stats['seconds']}s")
    return stats


def _filter(args, cfg) -> metrics.Filter:
    since = args.since
    if args.days and not since:
        since = (datetime.now(ZoneInfo(cfg.timezone)).date() - timedelta(days=args.days - 1)).isoformat()
    return metrics.Filter(since=since, until=args.until, host=args.host, profile=args.profile,
                          project=args.project, model=args.model)


def _dur(s: float) -> str:
    s = int(round(s or 0))
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def _num(n: float) -> str:
    n = n or 0
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def _table(headers, rows):
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    line = lambda cells: "  ".join(str(c).rjust(w) if i else str(c).ljust(w) for i, (c, w) in enumerate(zip(cells, widths)))  # noqa: E731
    print(line(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(line(r))


def cmd_report(args, cfg):
    conn = store.connect(cfg.db, readonly=True)
    f = _filter(args, cfg)
    rows = metrics.summary(conn, f, args.group, args.by)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    head = ["bucket"] + ([args.by] if args.by else []) + ["sess", "prompts", "subag", "peak", "tools", "err",
                                                         "out", "out(sub)", "in+cw", "cache rd", "claude", "api", "agent"]
    table = []
    for r in rows:
        m, s = r["tokens"]["main"], r["tokens"]["sub"]
        table.append([r["bucket"]] + ([r.get(args.by) or "-"] if args.by else []) + [
            r["sessions"], r["prompts"], r["subagents"], r["peak_concurrent_subagents"], r["tool_calls"], r["tool_errors"],
            _num(m["output"]), _num(s["output"]), _num(m["input"] + m["cache_write"] + s["input"] + s["cache_write"]),
            _num(m["cache_read"] + s["cache_read"]), _dur(r["claude_s"]), _dur(r["api_s"]), _dur(r["agent_s"])])
    _table(head, table)
    if args.tools:
        t = metrics.tools(conn, f, args.tools)
        print()
        _table(["tool", "calls", "errors", "bg", "in subagents"], [[r["name"], r["calls"], r["errors"], r["background"], r["in_subagents"]] for r in t["by_name"]])
        print()
        _table(["bash exe", "calls", "errors", "bg"], [[r["exe"], r["calls"], r["errors"], r["background"]] for r in t["bash"]])


def cmd_sessions(args, cfg):
    conn = store.connect(cfg.db, readonly=True)
    rows = metrics.sessions(conn, _filter(args, cfg), args.limit)
    if args.json:
        print(json.dumps(rows, indent=1))
        return
    tz = ZoneInfo(cfg.timezone)
    _table(["last active", "host/profile", "project", "title", "prompts", "subag", "tools", "out", "claude", "agent"],
           [[datetime.fromtimestamp(r["last_ts"], tz).strftime("%m-%d %H:%M") if r["last_ts"] else "-",
             f"{r['host']}/{r['profile']}", r["project"], (r["title"] or "")[:28], r["prompts"], r["subagents"],
             r["tool_calls"], _num(r["tokens"]["output"]), _dur(r["claude_s"]), _dur(r["agent_s"])] for r in rows])


def cmd_check(args, cfg):
    conn = store.connect(cfg.db, readonly=True)
    bad = 0
    for r in metrics.cost_check(conn):
        flags = [k for k in ("output", "cache_read", "cache_write") if r[k][0] > r[k][1]]
        bad += bool(flags)
        exact = all(r[k][0] == r[k][1] for k in ("output", "cache_read", "cache_write"))
        print(f"{'OVER ' + ','.join(flags) if flags else ('exact' if exact else 'ok   ')}  {r['session']}  {r['model']}  "
              + "  ".join(f"{k}={r[k][0]}/{r[k][1]}" for k in ("output", "cache_read", "cache_write")))
    print(f"{bad} session/model rows where transcript sums exceed Claude Code's cost-state")
    sys.exit(1 if bad else 0)


def cmd_host(args, cfg):
    if args.action == "add":
        if not args.name or not args.pubkey:
            raise SystemExit("usage: ccstats host add NAME 'ssh-ed25519 AAAA...'")
        line = hosts.add(cfg, args.name, " ".join(args.pubkey))
        print(f"registered {args.name}: it may now rsync into {cfg.archive}/{args.name}/ and nothing else\n  {line}")
    elif args.action == "rm":
        print("removed" if hosts.remove(args.name) else f"no push host named {args.name}")
    else:
        status = collect.load_status(cfg)
        print("push hosts (ssh key registered):", ", ".join(hosts.registered()) or "none")
        print("pull sources:", ", ".join(p.name for p in cfg.pulls) or "none")
        for name, st in sorted(status.items()):
            ok = st.get("last_success")
            print(f"  {name}: last ok {datetime.fromtimestamp(ok).strftime('%Y-%m-%d %H:%M') if ok else 'never'}"
                  + (f"  error: {st['error']}" if st.get("error") else ""))


def main(argv=None):
    p = argparse.ArgumentParser(prog="ccstats", description="Cross-machine Claude Code usage stats")
    p.add_argument("--config", help="config file (default ~/.config/ccstats/config.toml)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-q", "--quiet", action="store_true", help="only summary lines and errors")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="snapshot local profiles and run ssh pulls, then compute")
    c.add_argument("--no-compute", action="store_true")
    c.add_argument("--refresh-url", help="instead of computing here, POST to this server's /api/refresh")
    pl = sub.add_parser("pull", help="fetch one pull source (or this host) now")
    pl.add_argument("name")
    pl.add_argument("--via", help="ssh target to use instead of the configured ones")
    cp = sub.add_parser("compute", help="parse new or changed archive files into the database")
    cp.add_argument("--full", action="store_true", help="re-parse everything")

    def filters(sp):
        sp.add_argument("--days", type=int, help="last N local days")
        sp.add_argument("--since")
        sp.add_argument("--until")
        sp.add_argument("--host")
        sp.add_argument("--profile")
        sp.add_argument("--project")
        sp.add_argument("--model")
        sp.add_argument("--json", action="store_true")

    r = sub.add_parser("report", help="terminal tables")
    filters(r)
    g = r.add_mutually_exclusive_group()
    g.add_argument("--week", dest="group", action="store_const", const="week")
    g.add_argument("--month", dest="group", action="store_const", const="month")
    g.add_argument("--total", dest="group", action="store_const", const="total")
    r.add_argument("--by", choices=[*metrics.DIMENSIONS, "model"])
    r.add_argument("--tools", type=int, nargs="?", const=15, default=0, help="also list the top N tools")
    r.set_defaults(group="day")
    s = sub.add_parser("sessions", help="recent sessions")
    filters(s)
    s.add_argument("--limit", type=int, default=30)
    sub.add_parser("check", help="compare token sums with Claude Code's cost-state per session")
    sv = sub.add_parser("serve", help="run the HTTP API and dashboard")
    sv.add_argument("--listen", help="address:port (default from config, 0.0.0.0:8420)")
    h = sub.add_parser("host", help="manage push hosts (restricted ssh keys)")
    h.add_argument("action", choices=["add", "rm", "ls"])
    h.add_argument("name", nargs="?")
    h.add_argument("pubkey", nargs="*")
    sc = sub.add_parser("script", help="print a remote helper script")
    sc.add_argument("which", choices=["push", "cloudlab-setup"])

    args = p.parse_args(argv)
    # Under systemd stdout is a pipe; flush per line so the journal shows progress as it happens.
    sys.stdout.reconfigure(line_buffering=True)
    cfg = config.load(args.config)

    if args.cmd == "collect":
        collect.run(cfg, verbose=not args.quiet)
        if args.refresh_url:
            import urllib.request
            url = args.refresh_url.rstrip("/") + "/api/refresh"
            with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=10) as r:
                print(f"refresh: POST {url} -> {r.status}")
        elif not args.no_compute:
            _compute(cfg, quiet=args.quiet)
    elif args.cmd == "pull":
        res = collect.run(cfg, only=args.name, via=args.via, verbose=not args.quiet)
        if not res:
            raise SystemExit(f"unknown source {args.name}; configured: {cfg.host}, " + ", ".join(p.name for p in cfg.pulls))
        _compute(cfg, quiet=args.quiet)
    elif args.cmd == "compute":
        _compute(cfg, full=args.full, quiet=args.quiet)
    elif args.cmd == "report":
        cmd_report(args, cfg)
    elif args.cmd == "sessions":
        cmd_sessions(args, cfg)
    elif args.cmd == "check":
        cmd_check(args, cfg)
    elif args.cmd == "serve":
        from .server import serve
        serve(cfg, args.listen)
    elif args.cmd == "host":
        if args.action in ("add", "rm") and not args.name:
            raise SystemExit(f"usage: ccstats host {args.action} NAME")
        cmd_host(args, cfg)
    elif args.cmd == "script":
        name = "push.sh" if args.which == "push" else "cloudlab-setup.sh"
        body = resources.files("ccstats").joinpath(f"remote/{name}").read_text()
        port = cfg.listen.rpartition(":")[2]
        sys.stdout.write(body.replace("@HUB_SSH@", cfg.hub_ssh).replace("@HUB_URL@", f"http://{cfg.host}:{port}"))


if __name__ == "__main__":
    main()
