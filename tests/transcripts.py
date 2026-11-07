"""Builders for transcript records shaped like Claude Code's (2.1.27x–2.1.31x) JSONL."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DAY = "2026-11-05"


def ts(clock: str, day: str = DAY) -> str:
    """'10:00:05.5' on `day` (UTC) as Claude Code writes it."""
    return datetime.fromisoformat(f"{day}T{clock}+00:00").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def usage(inp=2, out=100, think=10, cr=1000, cw=50, cw1h=50):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
            "cache_creation": {"ephemeral_1h_input_tokens": cw1h, "ephemeral_5m_input_tokens": cw - cw1h},
            "output_tokens_details": {"thinking_tokens": think}}


def prompt(clock, text="do the thing", uuid=None, mode="auto", day=DAY):
    return {"type": "user", "timestamp": ts(clock, day), "uuid": uuid or f"p-{clock}", "origin": {"kind": "human"},
            "permissionMode": mode, "cwd": "/home/u/dev/proj", "version": "2.1.319", "gitBranch": "main",
            "message": {"role": "user", "content": text}}


def api_request(clock, rid, day=DAY):
    return {"type": "api-request", "id": rid, "timestamp": ts(clock, day), "querySource": "repl_main_thread"}


def assistant(clock, mid, blocks, rid=None, model="claude-opus-5-5", u=None, day=DAY):
    r = {"type": "assistant", "timestamp": ts(clock, day), "uuid": f"a-{mid}-{clock}",
         "message": {"id": mid, "model": model, "role": "assistant", "content": blocks, "usage": u or usage()}}
    if rid:
        r["requestRef"] = rid
    return r


def tool_use(tid, name="Bash", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def text(s="ok"):
    return {"type": "text", "text": s}


def tool_result(clock, tid, is_error=False, tur=None, pd=None, day=DAY):
    r = {"type": "user", "timestamp": ts(clock, day), "uuid": f"r-{tid}",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "out", "is_error": is_error}]},
         "toolUseResult": tur if tur is not None else {"stdout": "out", "stderr": ""}}
    if pd is not None:
        r["permissionDecision"] = pd
    return r


def write(archive, rel, records, meta=None, tail=""):
    path = os.path.join(archive, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        f.write(tail)
    if meta is not None:
        with open(path[: -len(".jsonl")] + ".meta.json", "w") as f:
            json.dump(meta, f)
    return path
