"""Turn one archived transcript file into rows for the stats database.

A file is one thread: a main session (`<slug>/<session>.jsonl`) or a subagent
(`<slug>/<session>/subagents/agent-<id>.jsonl`). Parsing is a pure function of
the file bytes plus the settings in `ParseSettings`, so changing either and
re-running compute rebuilds every number.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Bump when parse output changes, so cached files are parsed again.
PARSER_VERSION = 1

# Tools whose tool_use -> tool_result interval is the human deciding, not Claude working.
DECISION_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})

# Permission modes in which a tool call may sit waiting for a human approval.
PROMPTING_MODES = frozenset({"default", "acceptEdits", "plan"})

# Wrappers skipped when naming the executable a Bash command runs.
_CMD_WRAPPERS = frozenset({"sudo", "env", "time", "nice", "nohup", "command", "exec", "builtin", "stdbuf", "ionice",
                           "do", "then", "else", "{", "!"})
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Shell steps that only set up the real command (`cd dir && make` counts as make).
_NAVIGATION = frozenset({"cd", "pushd", "popd", "export", "set", "source", ".", "true", "echo", "for", "while",
                         "until", "if", "elif", "case", "done", "fi", "esac", "}", "[", "[[", "test", "wait", "sleep"})


@dataclass(frozen=True)
class ParseSettings:
    tz: str = "UTC"
    approval_cap_s: float = 600.0

    def key(self) -> str:
        return f"v{PARSER_VERSION}|{self.tz}|{self.approval_cap_s:g}"


@dataclass
class Parsed:
    thread: dict
    messages: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    prompts: list[dict] = field(default_factory=list)
    busy: list[dict] = field(default_factory=list)


def iso_ts(s: str | None) -> float | None:
    """ISO-8601 timestamp (as written by Claude Code) to epoch seconds."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def local_day(ts: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).date().isoformat()


def iso_week(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def split_by_day(start: float, end: float, tz: ZoneInfo):
    """Yield (day, seconds) pieces of [start, end) cut at local midnights."""
    while start < end:
        d = datetime.fromtimestamp(start, tz).date()
        nxt = datetime.combine(d + timedelta(days=1), datetime.min.time(), tz).timestamp()
        piece_end = min(end, nxt)
        yield d.isoformat(), piece_end - start
        start = piece_end


def union(intervals):
    """Merge (start, end) pairs into sorted, non-overlapping intervals."""
    out: list[list[float]] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def bash_executable(cmd: str) -> str:
    """Name of the program a Bash tool command mainly runs (`git`, `cargo`, ...)."""
    fallback = None
    for seg in re.split(r"&&|\|\||[;|\n]", cmd):
        try:
            words = shlex.split(seg, comments=True)
        except ValueError:
            words = seg.split()
        i, after_wrapper = 0, False
        while i < len(words):
            w = words[i].lstrip("(")
            if _ASSIGN.match(w) or w in _CMD_WRAPPERS:
                i, after_wrapper = i + 1, True
            elif w == "timeout":
                i, after_wrapper = i + 2, True  # skip the duration too
            elif after_wrapper and w.startswith("-"):
                i += 1
            else:
                break
        if i >= len(words):
            continue
        exe = os.path.basename(words[i].strip("()")) or "?"
        if exe in _NAVIGATION:
            fallback = fallback or exe
            continue
        return exe
    return fallback or "?"


def thread_identity(rel: str) -> dict:
    """Host, profile, project slug, session and agent ids from an archive-relative path."""
    parts = rel.split("/")
    # <host>/<profile>/projects/<slug>/<session>.jsonl
    # <host>/<profile>/projects/<slug>/<session>/subagents/agent-<id>.jsonl
    host, profile = parts[0], parts[1]
    slug = parts[3] if len(parts) > 3 else ""
    name = parts[-1][: -len(".jsonl")]
    if "subagents" in parts[4:-1]:
        session_id = parts[4]
        agent_id = name[len("agent-"):] if name.startswith("agent-") else name
    else:
        session_id, agent_id = name, None
    return {"host": host, "profile": profile, "slug": slug, "session_id": session_id, "agent_id": agent_id}


def _is_human_prompt(r: dict) -> bool:
    if "origin" in r:
        return (r.get("origin") or {}).get("kind") == "human"
    # Transcripts from before `origin` existed: a typed prompt is a plain-text user record.
    if r.get("isMeta") or r.get("isSidechain") or r.get("toolUseResult") is not None:
        return False
    content = (r.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str) or not content.strip():
        return False
    return not content.lstrip().startswith(("<local-command", "<command-", "<task-notification", "<bash-", "Caveat:"))


def _read_meta(path: str) -> dict:
    meta_path = path[: -len(".jsonl")] + ".meta.json"
    try:
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def parse_file(path: str, rel: str, settings: ParseSettings) -> Parsed:
    tz = ZoneInfo(settings.tz)
    ident = thread_identity(rel)
    meta = _read_meta(path) if ident["agent_id"] else {}

    api_req: dict[str, float] = {}       # api-request id -> request start
    msgs: dict[str, dict] = {}           # message.id -> aggregated message
    tool_uses: dict[str, dict] = {}      # tool_use id -> call
    results: dict[str, dict] = {}        # tool_use id -> result
    prompts: list[dict] = []
    info = {"cwd": None, "version": None, "branch": None, "entrypoint": None, "title": None,
            "ai_title": None, "account": None, "cost": None, "compactions": 0}
    prev_ts: float | None = None
    first_ts: float | None = None
    last_ts: float | None = None
    mode: str | None = None

    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        f = None
    with f or open(os.devnull) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue  # a line still being written when the file was copied
            if not isinstance(r, dict):
                continue
            t = r.get("type")
            ts = iso_ts(r.get("timestamp"))
            for k_src, k_dst in (("cwd", "cwd"), ("version", "version"), ("gitBranch", "branch"), ("entrypoint", "entrypoint")):
                if r.get(k_src) and info[k_dst] is None:
                    info[k_dst] = r[k_src]
            if r.get("permissionMode"):
                mode = r["permissionMode"]

            if t == "assistant":
                m = r.get("message") or {}
                mid = m.get("id") or r.get("uuid")
                synthetic = m.get("model") == "<synthetic>" or r.get("isApiErrorMessage")
                if mid and ts is not None and not synthetic:
                    msg = msgs.get(mid)
                    if msg is None:
                        start = api_req.get(r.get("requestRef") or "")
                        msg = msgs[mid] = {"id": mid, "first": ts, "last": ts, "model": m.get("model") or "?",
                                           "start": start if start is not None else (prev_ts if prev_ts is not None else ts),
                                           "usage": {}}
                    msg["last"] = max(msg["last"], ts)
                    if m.get("usage"):
                        msg["usage"] = m["usage"]
                    for b in m.get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                            tool_uses[b["id"]] = {"id": b["id"], "name": b.get("name") or "?", "ts": ts,
                                                  "input": b.get("input") or {}, "mode": mode, "model": msg["model"]}
            elif t == "api-request":
                if r.get("id") and ts is not None:
                    api_req[r["id"]] = ts
            elif t == "user":
                content = (r.get("message") or {}).get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                            results[b["tool_use_id"]] = {"ts": ts, "is_error": bool(b.get("is_error")),
                                                         "tur": r.get("toolUseResult"),
                                                         "pd": r.get("permissionDecision")}
                if ts is not None and not ident["agent_id"] and _is_human_prompt(r):
                    prompts.append({"uuid": r.get("uuid") or f"{rel}#{len(prompts)}", "ts": ts})
            elif t == "system" and r.get("subtype") == "compact_boundary":
                info["compactions"] += 1
            elif t == "custom-title":
                info["title"] = r.get("customTitle") or info["title"]
            elif t == "ai-title":
                info["ai_title"] = r.get("aiTitle") or info["ai_title"]
            elif t == "session-owner":
                info["account"] = r.get("accountUuid") or info["account"]
            elif t == "cost-state":
                info["cost"] = r

            if ts is not None:
                prev_ts = ts
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)

    # Messages: one row per API response, usage taken once.
    out_msgs = []
    for msg in msgs.values():
        u = msg["usage"]
        cc = u.get("cache_creation") or {}
        out_msgs.append({
            "message_id": msg["id"], "ts": msg["first"], "day": local_day(msg["first"], tz), "model": msg["model"],
            "input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
            "thinking": int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0),
            "cache_read": int(u.get("cache_read_input_tokens") or 0),
            "cache_write": int(u.get("cache_creation_input_tokens") or 0),
            "cache_write_1h": int(cc.get("ephemeral_1h_input_tokens") or 0),
        })

    # Tool calls and their busy intervals.
    out_tools = []
    tool_iv = []
    for tu in tool_uses.values():
        res = results.get(tu["id"])
        tur = res["tur"] if res and isinstance(res["tur"], dict) else {}
        inp = tu["input"] if isinstance(tu["input"], dict) else {}
        background = bool(inp.get("run_in_background") or tur.get("isAsync") or tur.get("backgroundTaskId"))
        pd = res.get("pd") if res else None
        human_approved = isinstance(pd, dict) and str(pd.get("source", "")).startswith("user")
        decision = tu["name"] in DECISION_TOOLS
        duration = (res["ts"] - tu["ts"]) if res and res["ts"] is not None else None
        if duration is not None and not decision:
            capped = human_approved or (pd is None and tu["mode"] in PROMPTING_MODES)
            busy = min(duration, settings.approval_cap_s) if capped else duration
            if busy > 0:
                tool_iv.append((tu["ts"], tu["ts"] + busy))
        exe = bash_executable(str(inp.get("command", ""))) if tu["name"] == "Bash" and inp.get("command") else None
        out_tools.append({
            "tool_use_id": tu["id"], "ts": tu["ts"], "day": local_day(tu["ts"], tz), "name": tu["name"],
            "exe": exe, "model": tu["model"], "is_error": int(bool(res and res["is_error"])),
            "is_background": int(background), "is_decision": int(decision),
            "human_approved": int(human_approved), "duration": duration,
        })

    # Without an api-request record the start is guessed from the record before the
    # response; a guess over an hour before the response is a gap, not a request.
    api_iv = [(m["start"] if m["last"] - m["start"] <= 3600 else m["first"], m["last"]) for m in msgs.values()]
    busy_rows = _busy_rows(rel, prompts, union(api_iv + tool_iv), union(api_iv), msgs, tz)

    # Each prompt's Claude time, for per-prompt averages and longest turns.
    per_prompt: dict[str, list[float]] = {}
    for b in busy_rows:
        acc = per_prompt.setdefault(b["prompt_key"], [0.0, 0.0])
        acc[0] += b["claude_s"]
        acc[1] += b["api_s"]
    out_prompts = [{"uuid": p["uuid"], "ts": p["ts"], "day": local_day(p["ts"], tz),
                    "claude_s": per_prompt.get(p["uuid"], [0, 0])[0], "api_s": per_prompt.get(p["uuid"], [0, 0])[1]}
                   for p in prompts]

    models: dict[str, int] = {}
    for m in out_msgs:
        models[m["model"]] = models.get(m["model"], 0) + m["output"] + 1
    cost = info["cost"] or {}
    cwd = info["cwd"]
    thread = {
        **{k: ident[k] for k in ("host", "profile", "session_id", "agent_id")},
        "project": os.path.basename(cwd.rstrip("/")) if cwd else ident["slug"],
        "cwd": cwd or ident["slug"], "is_subagent": int(bool(ident["agent_id"])),
        "agent_type": meta.get("agentType") if ident["agent_id"] else None,
        "spawn_depth": meta.get("spawnDepth") if ident["agent_id"] else 0,
        "parent_tool_use_id": meta.get("toolUseId"), "description": meta.get("description"),
        "first_ts": first_ts, "last_ts": last_ts,
        "title": info["title"] or info["ai_title"], "cc_version": info["version"], "branch": info["branch"],
        "entrypoint": info["entrypoint"], "account": info["account"],
        "model": max(models, key=models.get) if models else None,
        "n_prompts": len(prompts), "n_compactions": info["compactions"],
        "cost_usd": cost.get("totalCostUSD"),
        "cost_state": json.dumps(cost.get("modelUsage")) if cost.get("modelUsage") else None,
    }
    return Parsed(thread=thread, messages=out_msgs, tools=out_tools, prompts=out_prompts, busy=busy_rows)


def _busy_rows(rel, prompts, claude_iv, api_iv, msgs, tz):
    """Busy seconds per (prompt, local day), for Claude time and API-only time.

    Intervals are cut at each human prompt, so work is credited to the prompt
    it followed; work before the first prompt (all of a subagent's) goes to a
    per-file key.
    """
    cuts = sorted(p["ts"] for p in prompts)
    keys = [p["uuid"] for p in sorted(prompts, key=lambda p: p["ts"])]
    acc: dict[tuple[str, str], list[float]] = {}

    def add(intervals, idx):
        for s, e in intervals:
            # Split at prompt boundaries that fall inside the interval.
            bounds = [s] + [c for c in cuts if s < c < e] + [e]
            for a, b in zip(bounds, bounds[1:]):
                pos = _last_le(cuts, a)
                key = keys[pos] if pos >= 0 else f"{rel}#pre"
                for day, sec in split_by_day(a, b, tz):
                    acc.setdefault((key, day), [0.0, 0.0])[idx] += sec

    add(claude_iv, 0)
    add(api_iv, 1)
    return [{"prompt_key": k, "day": d, "claude_s": v[0], "api_s": v[1]} for (k, d), v in acc.items()]


def _last_le(sorted_vals, x):
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1
