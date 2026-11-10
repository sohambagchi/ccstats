"""SQLite store: per-file parse results, refreshed incrementally from the archive."""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from .parse import ParseSettings, Parsed, iso_week, local_day, parse_file, thread_identity

SCHEMA_VERSION = 2

# Row tables and the key that identifies one record across files. A forked or copied
# session repeats earlier records, so each record counts once: the copy in the
# lexically first file wins. Winners are kept in u_<table>, refreshed only for the
# keys a refresh touched.
_COLUMNS = {
    "messages": ("path TEXT, message_id TEXT, ts REAL, day TEXT, week TEXT, model TEXT, input INT, output INT, "
                 "thinking INT, cache_read INT, cache_write INT, cache_write_1h INT", ("message_id",)),
    "tools": ("path TEXT, tool_use_id TEXT, ts REAL, day TEXT, week TEXT, name TEXT, exe TEXT, model TEXT, "
              "is_error INT, is_background INT, is_decision INT, human_approved INT, duration REAL", ("tool_use_id",)),
    "prompts": ("path TEXT, uuid TEXT, ts REAL, day TEXT, week TEXT, claude_s REAL, api_s REAL", ("uuid",)),
    "busy": ("path TEXT, prompt_key TEXT, day TEXT, week TEXT, claude_s REAL, api_s REAL", ("prompt_key", "day")),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INT, mtime REAL, key TEXT, parsed_at REAL);
CREATE TABLE IF NOT EXISTS threads(
  path TEXT PRIMARY KEY, host TEXT, profile TEXT, project TEXT, cwd TEXT, session_id TEXT, agent_id TEXT,
  is_subagent INT, agent_type TEXT, spawn_depth INT, parent_tool_use_id TEXT, description TEXT,
  first_ts REAL, last_ts REAL, title TEXT, cc_version TEXT, branch TEXT, entrypoint TEXT, account TEXT,
  model TEXT, n_prompts INT, n_compactions INT, cost_usd REAL, cost_state TEXT,
  is_fork INT, background INT, spans TEXT, parent_path TEXT);
CREATE INDEX IF NOT EXISTS threads_session ON threads(host, profile, session_id);
""" + "".join(f"""
CREATE TABLE IF NOT EXISTS {t}({cols}, PRIMARY KEY(path, {', '.join(key)}));
CREATE TABLE IF NOT EXISTS u_{t}({cols}, PRIMARY KEY({', '.join(key)}));
CREATE INDEX IF NOT EXISTS {t}_key ON {t}({', '.join(key)});
CREATE INDEX IF NOT EXISTS u_{t}_path ON u_{t}(path);
CREATE INDEX IF NOT EXISTS u_{t}_day ON u_{t}(day);
""" for t, (cols, key) in _COLUMNS.items()) + """
-- Per-thread totals over its de-duplicated records, rebuilt with its session.
CREATE TABLE IF NOT EXISTS thread_stats(
  path TEXT PRIMARY KEY, api_calls INT, input INT, output INT, thinking INT, cache_read INT, cache_write INT,
  tool_calls INT, tool_errors INT, agent_calls INT, claude_s REAL, api_s REAL);
-- One row per main session with its subagents folded in; rebuilt only when one of its files changes.
CREATE TABLE IF NOT EXISTS sessions(
  host TEXT, profile TEXT, session_id TEXT, path TEXT, project TEXT, cwd TEXT, title TEXT, model TEXT,
  cc_version TEXT, branch TEXT, first_ts REAL, last_ts REAL, first_day TEXT, last_day TEXT, prompts INT,
  compactions INT, cost_usd REAL, subagents INT, agent_types TEXT, depth_counts TEXT, forks INT,
  background_agents INT, max_spawn_depth INT, peak_concurrent_subagents INT, api_calls INT, tool_calls INT,
  tool_errors INT, input INT, output INT, thinking INT, cache_read INT, cache_write INT,
  claude_s REAL, api_s REAL, agent_s REAL, PRIMARY KEY(host, profile, session_id));
CREATE INDEX IF NOT EXISTS sessions_last ON sessions(last_ts);
"""

_ROW_TABLES = ("threads", "messages", "tools", "prompts", "busy")


def connect(db_path: str, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    else:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
        row = conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone() if has_meta else None
        if row is not None and int(row[0]) != SCHEMA_VERSION:
            # Everything here is derived from the archive: drop it and re-parse.
            for kind, name in conn.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table','view') "
                                           "AND name NOT LIKE 'sqlite_%'").fetchall():
                conn.execute(f"DROP {kind.upper()} IF EXISTS {name}")
            conn.commit()
            row = None
        conn.executescript(SCHEMA)
        if row is None:
            conn.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
            conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def compute_lock(data_dir: str):
    """Serialise writers (collect timer, server refresh) on one lock file."""
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "compute.lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def archive_files(archive: str):
    """Yield (abs path, archive-relative path) for every transcript in the archive."""
    for root, dirs, files in os.walk(archive):
        dirs[:] = [d for d in dirs if d not in ("tool-results", "memory", "file-history")]
        for name in files:
            if name.endswith(".jsonl"):
                p = os.path.join(root, name)
                rel = os.path.relpath(p, archive).replace(os.sep, "/")
                if len(rel.split("/")) >= 5 and rel.split("/")[2] == "projects":
                    yield p, rel


def _write(conn: sqlite3.Connection, rel: str, parsed: Parsed | None):
    """Replace one file's rows (or drop them when `parsed` is None), noting the record keys involved."""
    _note_dirty(conn, rel)
    for table in _ROW_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE path = ?", (rel,))
    if parsed is None:
        conn.execute("DELETE FROM files WHERE path = ?", (rel,))
        conn.execute("DELETE FROM thread_stats WHERE path = ?", (rel,))
        return
    th = {"path": rel, **parsed.thread}
    conn.execute(f"INSERT INTO threads({','.join(th)}) VALUES({','.join('?' * len(th))})", list(th.values()))
    for table, rows in (("messages", parsed.messages), ("tools", parsed.tools), ("prompts", parsed.prompts), ("busy", parsed.busy)):
        if not rows:
            continue
        cols = ["path", "week", *rows[0].keys()]
        sql = f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
        conn.executemany(sql, [[rel, iso_week(r["day"]), *r.values()] for r in rows])
    _note_dirty(conn, rel)


def _note_dirty(conn, rel):
    conn.execute("INSERT OR IGNORE INTO dirty_paths VALUES(?)", (rel,))
    for t, (_, key) in _COLUMNS.items():
        k = ", ".join(key)
        conn.execute(f"INSERT OR IGNORE INTO dirty_{t} SELECT {k} FROM {t} WHERE path = ?", (rel,))


def _start_dirty(conn):
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS dirty_paths(path TEXT PRIMARY KEY)")
    for t, (_, key) in _COLUMNS.items():
        conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS dirty_{t}({', '.join(key)}, PRIMARY KEY({', '.join(key)}))")
        conn.execute(f"DELETE FROM dirty_{t}")
    conn.execute("DELETE FROM dirty_paths")


def _rematerialize(conn) -> set[str]:
    """Re-pick the winning copy of every touched record; return the paths whose counts may have moved."""
    paths = {r[0] for r in conn.execute("SELECT path FROM dirty_paths")}
    for t, (_, key) in _COLUMNS.items():
        on = " AND ".join(f"x.{k} = d.{k}" for k in key)
        match = f"EXISTS (SELECT 1 FROM dirty_{t} d WHERE {on})"
        paths |= {r[0] for r in conn.execute(f"SELECT DISTINCT path FROM u_{t} x WHERE {match}")}
        conn.execute(f"DELETE FROM u_{t} WHERE ({', '.join(key)}) IN (SELECT {', '.join(key)} FROM dirty_{t})")
        same = " AND ".join(f"y.{k} = x.{k}" for k in key)
        conn.execute(f"""INSERT INTO u_{t} SELECT * FROM {t} x WHERE {match}
                         AND x.path = (SELECT MIN(y.path) FROM {t} y WHERE {same})""")
        paths |= {r[0] for r in conn.execute(f"SELECT DISTINCT path FROM u_{t} x WHERE {match}")}
    return paths


def _session_of(rel: str) -> tuple[str, str, str]:
    ident = thread_identity(rel)
    return ident["host"], ident["profile"], ident["session_id"]


def rollup_session(conn: sqlite3.Connection, host: str, profile: str, session_id: str, tz: ZoneInfo):
    """Rebuild one session's thread_stats, subagent parents and sessions row from the stored rows."""
    threads = conn.execute("SELECT * FROM threads WHERE host = ? AND profile = ? AND session_id = ?",
                           (host, profile, session_id)).fetchall()
    conn.execute("DELETE FROM sessions WHERE host = ? AND profile = ? AND session_id = ?", (host, profile, session_id))
    if not threads:
        return
    paths = [t["path"] for t in threads]
    q = ",".join("?" * len(paths))
    stats = {p: dict(api_calls=0, input=0, output=0, thinking=0, cache_read=0, cache_write=0, tool_calls=0,
                     tool_errors=0, agent_calls=0, claude_s=0.0, api_s=0.0) for p in paths}
    for r in conn.execute(f"""SELECT path, COUNT(*) n, SUM(input) i, SUM(output) o, SUM(thinking) th,
                              SUM(cache_read) cr, SUM(cache_write) cw FROM u_messages WHERE path IN ({q}) GROUP BY path""", paths):
        stats[r["path"]].update(api_calls=r["n"], input=r["i"] or 0, output=r["o"] or 0, thinking=r["th"] or 0,
                                cache_read=r["cr"] or 0, cache_write=r["cw"] or 0)
    for r in conn.execute(f"""SELECT path, COUNT(*) n, SUM(is_error) e, SUM(name IN ('Agent', 'Task')) a
                              FROM u_tools WHERE path IN ({q}) GROUP BY path""", paths):
        stats[r["path"]].update(tool_calls=r["n"], tool_errors=r["e"] or 0, agent_calls=r["a"] or 0)
    for r in conn.execute(f"SELECT path, SUM(claude_s) c, SUM(api_s) a FROM u_busy WHERE path IN ({q}) GROUP BY path", paths):
        stats[r["path"]].update(claude_s=r["c"] or 0.0, api_s=r["a"] or 0.0)
    for p, st in stats.items():
        conn.execute(f"INSERT OR REPLACE INTO thread_stats(path, {', '.join(st)}) VALUES(?{',?' * len(st)})", [p, *st.values()])

    # A subagent's parent is the thread whose Agent call spawned it: the main thread or another subagent.
    subs = [t for t in threads if t["is_subagent"]]
    for s in subs:
        parent = None
        if s["parent_tool_use_id"]:
            row = conn.execute(f"SELECT path FROM tools WHERE tool_use_id = ? AND path IN ({q}) ORDER BY length(path), path LIMIT 1",
                               [s["parent_tool_use_id"], *paths]).fetchone()
            parent = row[0] if row else None
        conn.execute("UPDATE threads SET parent_path = ? WHERE path = ?", (parent, s["path"]))

    main = next((t for t in threads if not t["is_subagent"]), None)
    if main is None:
        return  # subagent files whose main transcript never arrived
    from .metrics import peak_concurrency
    total = {k: sum(stats[p][k] for p in paths) for k in ("api_calls", "input", "output", "thinking", "cache_read",
                                                           "cache_write", "tool_calls", "tool_errors")}
    types: dict[str, int] = {}
    depths: dict[str, int] = {}
    for s in subs:
        types[s["agent_type"] or "?"] = types.get(s["agent_type"] or "?", 0) + 1
        d = str(s["spawn_depth"] or 1)
        depths[d] = depths.get(d, 0) + 1
    row = {
        "host": host, "profile": profile, "session_id": session_id, "path": main["path"], "project": main["project"],
        "cwd": main["cwd"], "title": main["title"], "model": main["model"], "cc_version": main["cc_version"],
        "branch": main["branch"], "first_ts": main["first_ts"], "last_ts": main["last_ts"],
        "first_day": local_day(main["first_ts"], tz) if main["first_ts"] else None,
        "last_day": local_day(main["last_ts"], tz) if main["last_ts"] else None,
        "prompts": main["n_prompts"], "compactions": main["n_compactions"], "cost_usd": main["cost_usd"],
        "subagents": len(subs), "agent_types": json.dumps(types), "depth_counts": json.dumps(depths),
        "forks": sum(s["is_fork"] or 0 for s in subs), "background_agents": sum(s["background"] or 0 for s in subs),
        "max_spawn_depth": max((s["spawn_depth"] or 1 for s in subs), default=0),
        "peak_concurrent_subagents": peak_concurrency(subs), **total,
        "claude_s": stats[main["path"]]["claude_s"], "api_s": stats[main["path"]]["api_s"],
        "agent_s": sum(stats[s["path"]]["claude_s"] for s in subs),
    }
    conn.execute(f"INSERT INTO sessions({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))


def refresh(conn: sqlite3.Connection, archive: str, settings: ParseSettings, full: bool = False, log=None) -> dict:
    """Parse files that are new or changed since their last parse; drop rows of vanished files.

    Everything downstream of the changed files (de-duplicated rows, per-thread and
    per-session totals) is rebuilt in the same transaction, so readers see either
    the old numbers or the new ones and unchanged sessions are never recomputed.
    """
    key = settings.key()
    tz = ZoneInfo(settings.tz)
    known = {r["path"]: (r["size"], r["mtime"], r["key"]) for r in conn.execute("SELECT path, size, mtime, key FROM files")}
    seen = set()
    stats = {"parsed": 0, "unchanged": 0, "removed": 0, "sessions": 0}
    t0 = time.time()
    with conn:
        _start_dirty(conn)
        for path, rel in archive_files(archive):
            seen.add(rel)
            try:
                st = os.stat(path)
            except OSError:
                continue
            # A subagent's meta.json can land after its transcript; include it in the signature.
            mtime = st.st_mtime
            meta = path[: -len(".jsonl")] + ".meta.json"
            if os.path.exists(meta):
                mtime = max(mtime, os.stat(meta).st_mtime)
            if not full and known.get(rel) == (st.st_size, mtime, key):
                stats["unchanged"] += 1
                continue
            _write(conn, rel, parse_file(path, rel, settings))
            conn.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)", (rel, st.st_size, mtime, key, time.time()))
            stats["parsed"] += 1
            if log:
                log(f"parsed {rel}")
        for rel in set(known) - seen:
            _write(conn, rel, None)
            stats["removed"] += 1
        sessions = {_session_of(p) for p in _rematerialize(conn)}
        for s in sorted(sessions):
            rollup_session(conn, *s, tz)
        stats["sessions"] = len(sessions)
        now = str(time.time())
        conn.execute("INSERT OR REPLACE INTO meta VALUES('computed_at', ?)", (now,))
        conn.execute("INSERT OR REPLACE INTO meta VALUES('timezone', ?)", (settings.tz,))
        if stats["parsed"] or stats["removed"]:
            conn.execute("INSERT OR REPLACE INTO meta VALUES('data_version', ?)", (now,))
    stats["seconds"] = round(time.time() - t0, 2)
    return stats


def data_version(conn: sqlite3.Connection) -> str:
    """Changes exactly when stored numbers may have changed; keys the server's response cache."""
    row = conn.execute("SELECT v FROM meta WHERE k='data_version'").fetchone()
    return row[0] if row else "0"
