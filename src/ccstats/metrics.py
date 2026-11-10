"""Aggregations over the store, shared by the CLI report and the HTTP API.

Every function takes a `Filter` and returns plain dicts. Time buckets are local
days (`day`), ISO weeks (`week`), months (`month`) or one bucket (`total`).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

GROUPS = ("day", "week", "month", "total")
DIMENSIONS = ("host", "profile", "project")


@dataclass
class Filter:
    since: str | None = None      # first local day, inclusive (YYYY-MM-DD)
    until: str | None = None      # last local day, inclusive
    host: str | None = None
    profile: str | None = None
    project: str | None = None
    model: str | None = None      # applies to tokens and tool calls

    def where(self, alias: str, day_col: str = "day", model_col: str | None = None) -> tuple[str, list]:
        """SQL condition and parameters for rows `alias` joined to threads `th`."""
        cond, args = ["1=1"], []
        if self.since:
            cond.append(f"{alias}.{day_col} >= ?")
            args.append(self.since)
        if self.until:
            cond.append(f"{alias}.{day_col} <= ?")
            args.append(self.until)
        for dim in DIMENSIONS:
            val = getattr(self, dim)
            if val:
                cond.append(f"th.{dim} = ?")
                args.append(val)
        if self.model and model_col:
            cond.append(f"{alias}.{model_col} = ?")
            args.append(self.model)
        return " AND ".join(cond), args


def _bucket(group: str, alias: str) -> str:
    return {"day": f"{alias}.day", "week": f"{alias}.week", "month": f"substr({alias}.day, 1, 7)", "total": "'total'"}[group]


def _dim(by: str | None, alias: str = "th") -> str:
    if by is None:
        return "''"
    if by == "model":
        raise ValueError("model")
    return f"{alias}.{by}"


def summary(conn: sqlite3.Connection, f: Filter, group: str = "day", by: str | None = None) -> list[dict]:
    """One row per (bucket, dimension value) with sessions, tokens, tools and time."""
    if group not in GROUPS:
        raise ValueError(f"group must be one of {GROUPS}")
    if by is not None and by not in (*DIMENSIONS, "model"):
        raise ValueError(f"by must be one of {DIMENSIONS + ('model',)}")
    rows: dict[tuple, dict] = defaultdict(_empty)

    # Tokens, main and subagent threads separately.
    dim = "m.model" if by == "model" else _dim(by)
    w, a = f.where("m", model_col="model")
    for r in conn.execute(f"""
        SELECT {_bucket(group, 'm')} AS b, {dim} AS d, th.is_subagent AS sub, COUNT(*) AS n,
               SUM(input) AS input, SUM(output) AS output, SUM(thinking) AS thinking,
               SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write
        FROM u_messages m JOIN threads th ON th.path = m.path WHERE {w} GROUP BY b, d, sub""", a):
        row = rows[(r["b"], r["d"])]
        side = "sub" if r["sub"] else "main"
        row[f"api_calls_{side}"] += r["n"]
        for k in ("input", "output", "thinking", "cache_read", "cache_write"):
            row["tokens"][side][k] += r[k] or 0

    # Tool calls.
    dim = "t.model" if by == "model" else _dim(by)
    w, a = f.where("t", model_col="model")
    for r in conn.execute(f"""
        SELECT {_bucket(group, 't')} AS b, {dim} AS d, COUNT(*) AS n, SUM(is_error) AS err,
               SUM(is_background) AS bg, SUM(th.is_subagent) AS sub
        FROM u_tools t JOIN threads th ON th.path = t.path WHERE {w} GROUP BY b, d""", a):
        row = rows[(r["b"], r["d"])]
        row["tool_calls"] += r["n"]
        row["tool_errors"] += r["err"] or 0
        row["tool_background"] += r["bg"] or 0
        row["tool_calls_sub"] += r["sub"] or 0

    if by != "model":
        dim = _dim(by)
        # Human prompts.
        w, a = f.where("p")
        for r in conn.execute(f"""
            SELECT {_bucket(group, 'p')} AS b, {dim} AS d, COUNT(*) AS n
            FROM u_prompts p JOIN threads th ON th.path = p.path WHERE {w} GROUP BY b, d""", a):
            rows[(r["b"], r["d"])]["prompts"] += r["n"]

        # Claude time (main threads) and agent time (subagents, summed in parallel).
        w, a = f.where("u")
        for r in conn.execute(f"""
            SELECT {_bucket(group, 'u')} AS b, {dim} AS d, th.is_subagent AS sub,
                   SUM(claude_s) AS c, SUM(api_s) AS api
            FROM u_busy u JOIN threads th ON th.path = u.path WHERE {w} GROUP BY b, d, sub""", a):
            row = rows[(r["b"], r["d"])]
            if r["sub"]:
                row["agent_s"] += r["c"] or 0
                row["agent_api_s"] += r["api"] or 0
            else:
                row["claude_s"] += r["c"] or 0
                row["api_s"] += r["api"] or 0

        # Sessions active in the bucket: main threads with any message or prompt there.
        w, a = f.where("x")
        for r in conn.execute(f"""
            SELECT {_bucket(group, 'x')} AS b, {dim} AS d, COUNT(DISTINCT th.path) AS n
            FROM (SELECT path, day, week FROM u_messages UNION SELECT path, day, week FROM u_prompts) x
            JOIN threads th ON th.path = x.path WHERE th.is_subagent = 0 AND {w} GROUP BY b, d""", a):
            rows[(r["b"], r["d"])]["sessions"] += r["n"]

        _add_teams(conn, f, group, by, rows)

    out = []
    for (b, d), row in sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        t = row["tokens"]
        for side in ("main", "sub"):
            s = t[side]
            s["total"] = s["input"] + s["output"] + s["cache_read"] + s["cache_write"]
            s["total_no_cache_read"] = s["total"] - s["cache_read"]
        row["bucket"] = b
        if by:
            row[by] = d
        out.append(row)
    return out


def _empty() -> dict:
    tok = lambda: {"input": 0, "output": 0, "thinking": 0, "cache_read": 0, "cache_write": 0}  # noqa: E731
    return {"sessions": 0, "prompts": 0, "subagents": 0, "sessions_with_subagents": 0, "agent_types": {},
            "max_spawn_depth": 0, "mean_spawn_depth": 0.0, "peak_concurrent_subagents": 0,
            "api_calls_main": 0, "api_calls_sub": 0, "tokens": {"main": tok(), "sub": tok()},
            "tool_calls": 0, "tool_calls_sub": 0, "tool_errors": 0, "tool_background": 0,
            "claude_s": 0.0, "api_s": 0.0, "agent_s": 0.0, "agent_api_s": 0.0}


def _add_teams(conn, f: Filter, group: str, by: str | None, rows):
    """Subagent counts, types, depth and peak concurrency, bucketed by spawn day."""
    from .parse import iso_week, local_day  # local import keeps metrics free of tz state
    tz = _tz(conn)
    subs = conn.execute("SELECT * FROM threads WHERE is_subagent = 1 AND first_ts IS NOT NULL").fetchall()
    per_bucket: dict[tuple, list] = defaultdict(list)
    for s in subs:
        day = local_day(s["first_ts"], tz)
        if (f.since and day < f.since) or (f.until and day > f.until):
            continue
        if any(getattr(f, dim) and s[dim] != getattr(f, dim) for dim in DIMENSIONS):
            continue
        b = {"day": day, "week": iso_week(day), "month": day[:7], "total": "total"}[group]
        per_bucket[(b, s[by] if by else "")].append(s)
    for key, items in per_bucket.items():
        row = rows[key]
        row["subagents"] = len(items)
        sessions = defaultdict(list)
        for s in items:
            sessions[(s["host"], s["profile"], s["session_id"])].append(s)
            t = s["agent_type"] or "?"
            row["agent_types"][t] = row["agent_types"].get(t, 0) + 1
        row["sessions_with_subagents"] = len(sessions)
        depths = [s["spawn_depth"] or 1 for s in items]
        row["max_spawn_depth"] = max(depths)
        row["mean_spawn_depth"] = round(sum(depths) / len(depths), 2)
        row["peak_concurrent_subagents"] = max(peak_concurrency(v) for v in sessions.values())


def peak_concurrency(threads) -> int:
    events = []
    for t in threads:
        if t["first_ts"] is not None and t["last_ts"] is not None:
            events += [(t["first_ts"], 1), (t["last_ts"], -1)]
    best = cur = 0
    for _, delta in sorted(events, key=lambda e: (e[0], e[1])):
        cur += delta
        best = max(best, cur)
    return best


def _tz(conn):
    from zoneinfo import ZoneInfo
    row = conn.execute("SELECT v FROM meta WHERE k='timezone'").fetchone()
    return ZoneInfo(row[0] if row else "UTC")


def tools(conn: sqlite3.Connection, f: Filter, top: int = 25) -> dict:
    """Tool calls by name and Bash commands by executable."""
    w, a = f.where("t", model_col="model")
    by_name = [dict(r) for r in conn.execute(f"""
        SELECT name, COUNT(*) AS calls, SUM(is_error) AS errors, SUM(is_background) AS background,
               SUM(th.is_subagent) AS in_subagents, ROUND(SUM(COALESCE(duration, 0)), 1) AS seconds
        FROM u_tools t JOIN threads th ON th.path = t.path WHERE {w}
        GROUP BY name ORDER BY calls DESC LIMIT ?""", [*a, top])]
    bash = [dict(r) for r in conn.execute(f"""
        SELECT exe, COUNT(*) AS calls, SUM(is_error) AS errors, SUM(is_background) AS background
        FROM u_tools t JOIN threads th ON th.path = t.path WHERE {w} AND name = 'Bash'
        GROUP BY exe ORDER BY calls DESC LIMIT ?""", [*a, top])]
    return {"by_name": by_name, "bash": bash}


def models(conn: sqlite3.Connection, f: Filter) -> list[dict]:
    w, a = f.where("m", model_col="model")
    return [dict(r) for r in conn.execute(f"""
        SELECT m.model, COUNT(*) AS api_calls, SUM(input) AS input, SUM(output) AS output, SUM(thinking) AS thinking,
               SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write
        FROM u_messages m JOIN threads th ON th.path = m.path WHERE {w}
        GROUP BY m.model ORDER BY output DESC""", a)]


SESSION_SORTS = {"recent": "last_ts DESC", "subagents": "subagents DESC, last_ts DESC",
                 "claude": "claude_s DESC", "agent": "agent_s DESC", "output": "output DESC"}


def _session_out(r) -> dict:
    d = dict(r)
    d["tokens"] = {k: d.pop(k) for k in ("input", "output", "thinking", "cache_read", "cache_write")}
    d["agent_types"] = json.loads(d["agent_types"] or "{}")
    d["depth_counts"] = json.loads(d["depth_counts"] or "{}")
    return d


def sessions(conn: sqlite3.Connection, f: Filter, limit: int = 100, sort: str = "recent") -> list[dict]:
    """Main sessions active in the range, with their subagents folded in (precomputed rollups)."""
    if sort not in SESSION_SORTS:
        raise ValueError(f"sort must be one of {tuple(SESSION_SORTS)}")
    cond, args = ["1=1"], []
    if f.since:
        cond.append("last_day >= ?")
        args.append(f.since)
    if f.until:
        cond.append("first_day <= ?")
        args.append(f.until)
    for dim in DIMENSIONS:
        if getattr(f, dim):
            cond.append(f"{dim} = ?")
            args.append(getattr(f, dim))
    rows = conn.execute(f"SELECT * FROM sessions WHERE {' AND '.join(cond)} ORDER BY {SESSION_SORTS[sort]} LIMIT ?",
                        [*args, limit]).fetchall()
    return [_session_out(r) for r in rows]


def session_detail(conn: sqlite3.Connection, host: str, profile: str, session_id: str) -> dict | None:
    """One session: its rollup, every thread (main + subagents) as a tree with stats and activity spans,
    and the main thread's prompts, for the hierarchy and timeline views."""
    row = conn.execute("SELECT * FROM sessions WHERE host = ? AND profile = ? AND session_id = ?",
                       (host, profile, session_id)).fetchone()
    if row is None:
        return None
    threads = conn.execute("""
        SELECT th.path, th.agent_id, th.is_subagent, th.agent_type, th.spawn_depth, th.description, th.first_ts,
               th.last_ts, th.model, th.is_fork, th.background, th.spans, th.parent_path, s.*
        FROM threads th LEFT JOIN thread_stats s ON s.path = th.path
        WHERE th.host = ? AND th.profile = ? AND th.session_id = ?""", (host, profile, session_id)).fetchall()
    ids = {t["path"]: (t["agent_id"] or "main") for t in threads}
    out = []
    for t in threads:
        d = {k: t[k] for k in t.keys() if k != "path"}
        d["id"] = ids[t["path"]]
        d["parent"] = ids.get(t["parent_path"]) if t["is_subagent"] else None
        if t["is_subagent"] and d["parent"] is None:
            d["parent"] = "main"  # spawning call not found (e.g. compacted away): hang it off the main thread
        d["spans"] = json.loads(t["spans"] or "[]")
        out.append(d)
    out.sort(key=lambda d: (d["is_subagent"], d["first_ts"] or 0))
    prompts = [dict(r) for r in conn.execute("SELECT ts, claude_s FROM u_prompts WHERE path = ? ORDER BY ts", (row["path"],))]
    return {"session": _session_out(row), "threads": out, "prompts": prompts}


def dimensions(conn: sqlite3.Connection) -> dict:
    """Distinct values for filter dropdowns, plus the covered day range."""
    out = {d: [r[0] for r in conn.execute(f"SELECT DISTINCT {d} FROM threads WHERE {d} IS NOT NULL ORDER BY 1")] for d in DIMENSIONS}
    out["model"] = [r[0] for r in conn.execute("SELECT DISTINCT model FROM messages ORDER BY 1")]
    r = conn.execute("SELECT MIN(day), MAX(day) FROM messages").fetchone()
    out["first_day"], out["last_day"] = r[0], r[1]
    return out


def hosts(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("""
        SELECT host, profile, COUNT(*) FILTER (WHERE is_subagent = 0) AS sessions,
               COUNT(*) FILTER (WHERE is_subagent = 1) AS subagents, MIN(first_ts) AS first_ts, MAX(last_ts) AS last_ts
        FROM threads GROUP BY host, profile ORDER BY last_ts DESC""")]


def cost_check(conn: sqlite3.Connection) -> list[dict]:
    """Per-session token sums from messages next to Claude Code's own cumulative cost-state.

    cost-state also counts side requests that leave no assistant record (titles,
    summaries), so it may exceed the transcript sums; it should never be lower.
    """
    import json
    out = []
    for th in conn.execute("SELECT * FROM threads WHERE is_subagent = 0 AND cost_state IS NOT NULL"):
        mine = defaultdict(lambda: defaultdict(int))
        for r in conn.execute("""SELECT m.model, SUM(output) o, SUM(cache_read) cr, SUM(cache_write) cw, SUM(input) i
                                 FROM messages m JOIN threads t ON t.path = m.path
                                 WHERE t.host = ? AND t.profile = ? AND t.session_id = ? GROUP BY m.model""",
                              (th["host"], th["profile"], th["session_id"])):
            mine[r["model"]] = {"output": r["o"], "cache_read": r["cr"], "cache_write": r["cw"], "input": r["i"]}
        theirs = json.loads(th["cost_state"])
        for model, u in theirs.items():
            base = model.split("[")[0]
            m = mine.get(base, {})
            out.append({"session": f"{th['host']}/{th['profile']}/{th['session_id']}", "model": model,
                        "output": (m.get("output", 0), u.get("outputTokens", 0)),
                        "cache_read": (m.get("cache_read", 0), u.get("cacheReadInputTokens", 0)),
                        "cache_write": (m.get("cache_write", 0), u.get("cacheCreationInputTokens", 0))})
    return out
