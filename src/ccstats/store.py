"""SQLite store: per-file parse results, refreshed incrementally from the archive."""
from __future__ import annotations

import fcntl
import os
import sqlite3
import time
from contextlib import contextmanager

from .parse import ParseSettings, Parsed, iso_week, parse_file

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INT, mtime REAL, key TEXT, parsed_at REAL);
CREATE TABLE IF NOT EXISTS threads(
  path TEXT PRIMARY KEY, host TEXT, profile TEXT, project TEXT, cwd TEXT, session_id TEXT, agent_id TEXT,
  is_subagent INT, agent_type TEXT, spawn_depth INT, parent_tool_use_id TEXT, description TEXT,
  first_ts REAL, last_ts REAL, title TEXT, cc_version TEXT, branch TEXT, entrypoint TEXT, account TEXT,
  model TEXT, n_prompts INT, n_compactions INT, cost_usd REAL, cost_state TEXT);
CREATE TABLE IF NOT EXISTS messages(
  path TEXT, message_id TEXT, ts REAL, day TEXT, week TEXT, model TEXT, input INT, output INT, thinking INT,
  cache_read INT, cache_write INT, cache_write_1h INT, PRIMARY KEY(path, message_id));
CREATE TABLE IF NOT EXISTS tools(
  path TEXT, tool_use_id TEXT, ts REAL, day TEXT, week TEXT, name TEXT, exe TEXT, model TEXT, is_error INT,
  is_background INT, is_decision INT, human_approved INT, duration REAL, PRIMARY KEY(path, tool_use_id));
CREATE TABLE IF NOT EXISTS prompts(
  path TEXT, uuid TEXT, ts REAL, day TEXT, week TEXT, claude_s REAL, api_s REAL, PRIMARY KEY(path, uuid));
CREATE TABLE IF NOT EXISTS busy(
  path TEXT, prompt_key TEXT, day TEXT, week TEXT, claude_s REAL, api_s REAL, PRIMARY KEY(path, prompt_key, day));
CREATE INDEX IF NOT EXISTS messages_day ON messages(day);
CREATE INDEX IF NOT EXISTS messages_id ON messages(message_id);
CREATE INDEX IF NOT EXISTS tools_day ON tools(day);
CREATE INDEX IF NOT EXISTS busy_day ON busy(day);
CREATE INDEX IF NOT EXISTS threads_session ON threads(host, profile, session_id);

-- A forked or copied session repeats earlier records, so each API message, tool call,
-- prompt and busy slice counts once: the copy in the lexically first file wins.
CREATE VIEW IF NOT EXISTS u_messages AS SELECT * FROM (
  SELECT m.*, ROW_NUMBER() OVER (PARTITION BY message_id ORDER BY path) AS rn FROM messages m) WHERE rn = 1;
CREATE VIEW IF NOT EXISTS u_tools AS SELECT * FROM (
  SELECT t.*, ROW_NUMBER() OVER (PARTITION BY tool_use_id ORDER BY path) AS rn FROM tools t) WHERE rn = 1;
CREATE VIEW IF NOT EXISTS u_prompts AS SELECT * FROM (
  SELECT p.*, ROW_NUMBER() OVER (PARTITION BY uuid ORDER BY path) AS rn FROM prompts p) WHERE rn = 1;
CREATE VIEW IF NOT EXISTS u_busy AS SELECT * FROM (
  SELECT b.*, ROW_NUMBER() OVER (PARTITION BY prompt_key, day ORDER BY path) AS rn FROM busy b) WHERE rn = 1;
"""

_ROW_TABLES = ("threads", "messages", "tools", "prompts", "busy")


def connect(db_path: str, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    else:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
            conn.commit()
        elif int(row[0]) != SCHEMA_VERSION:
            raise SystemExit(f"{db_path}: schema {row[0]}, expected {SCHEMA_VERSION}; delete it to rebuild from the archive")
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


def _write(conn: sqlite3.Connection, rel: str, parsed: Parsed):
    for table in _ROW_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE path = ?", (rel,))
    th = {"path": rel, **parsed.thread}
    conn.execute(f"INSERT INTO threads({','.join(th)}) VALUES({','.join('?' * len(th))})", list(th.values()))
    for table, rows in (("messages", parsed.messages), ("tools", parsed.tools), ("prompts", parsed.prompts), ("busy", parsed.busy)):
        if not rows:
            continue
        cols = ["path", "week", *rows[0].keys()]
        sql = f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
        conn.executemany(sql, [[rel, iso_week(r["day"]), *r.values()] for r in rows])


def refresh(conn: sqlite3.Connection, archive: str, settings: ParseSettings, full: bool = False, log=None) -> dict:
    """Parse files that are new or changed since their last parse; drop rows of vanished files."""
    key = settings.key()
    known = {r["path"]: (r["size"], r["mtime"], r["key"]) for r in conn.execute("SELECT path, size, mtime, key FROM files")}
    seen = set()
    stats = {"parsed": 0, "unchanged": 0, "removed": 0}
    t0 = time.time()
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
        parsed = parse_file(path, rel, settings)
        with conn:
            _write(conn, rel, parsed)
            conn.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?)", (rel, st.st_size, mtime, key, time.time()))
        stats["parsed"] += 1
        if log:
            log(f"parsed {rel}")
    for rel in set(known) - seen:
        with conn:
            for table in (*_ROW_TABLES, "files"):
                conn.execute(f"DELETE FROM {table} WHERE path = ?", (rel,))
        stats["removed"] += 1
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES('computed_at', ?)", (str(time.time()),))
        conn.execute("INSERT OR REPLACE INTO meta VALUES('timezone', ?)", (settings.tz,))
    stats["seconds"] = round(time.time() - t0, 2)
    return stats
