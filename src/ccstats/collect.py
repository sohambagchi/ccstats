"""Copy transcripts into the archive: this machine's own, and the configured ssh pulls.

The archive is append-only: rsync runs without --delete, so transcripts that
Claude Code later cleans up on a host stay here.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from .config import Config, Pull

# Only transcripts and their small JSON sidecars; tool outputs and memory stay behind.
RSYNC_FILTERS = [
    "--prune-empty-dirs",
    "--exclude=tool-results/", "--exclude=memory/", "--exclude=file-history/",
    "--include=*/", "--include=*.jsonl", "--include=*.json", "--exclude=*",
]
SSH_OPTS = "ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15"


def _rsync(src: str, dst: str, ssh: bool) -> tuple[bool, str]:
    os.makedirs(dst, exist_ok=True)
    cmd = ["rsync", "-a", "--timeout=120", *RSYNC_FILTERS]
    if ssh:
        # Transcripts are JSON text and compress about 5x.
        cmd += ["-z", "-e", SSH_OPTS]
    cmd += [src.rstrip("/") + "/", dst.rstrip("/") + "/"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    # 23/24 = some files vanished or were unreadable mid-copy; the rest arrived.
    ok = p.returncode in (0, 23, 24)
    return ok, (p.stderr.strip().splitlines() or [""])[-1] if not ok else ""


def collect_local(cfg: Config) -> dict:
    """Snapshot this machine's profiles into archive/<host>/<profile>/projects."""
    out = {}
    for profile, cfg_dir in cfg.profiles.items():
        src = os.path.join(os.path.expanduser(cfg_dir), "projects")
        if not os.path.isdir(src):
            continue
        ok, err = _rsync(src, os.path.join(cfg.archive, cfg.host, profile, "projects"), ssh=False)
        out[f"{cfg.host}/{profile}"] = {"ok": ok, "error": err}
    return out


def pull(cfg: Config, src: Pull, via: str | None = None) -> dict:
    """Fetch one pull source, trying its ssh targets in order until one answers."""
    targets = [via] if via else src.ssh
    last_err = "no ssh targets"
    for target in targets:
        if src.kind == "cloudlab":
            ok, err = _rsync(f"{target}:{src.path}", cfg.archive, ssh=True)
        else:
            ok, err = True, ""
            for profile, cfg_dir in src.profiles.items():
                remote = cfg_dir.replace("~/", "", 1) if cfg_dir.startswith("~/") else cfg_dir
                p_ok, p_err = _rsync(f"{target}:{remote}/projects", os.path.join(cfg.archive, src.name, profile, "projects"), ssh=True)
                # A host may lack a profile; only a failure to reach it counts.
                if not p_ok and "No such file" not in p_err:
                    ok, err = False, p_err
        if ok:
            return {"ok": True, "via": target, "error": ""}
        last_err = f"{target}: {err}"
    return {"ok": False, "via": None, "error": last_err}


def run(cfg: Config, only: str | None = None, via: str | None = None, log=print) -> dict:
    status = load_status(cfg)
    now = time.time()
    results: dict[str, dict] = {}
    if only is None or only == cfg.host:
        results.update(collect_local(cfg))
    for src in cfg.pulls:
        if only is None or only == src.name:
            results[f"pull:{src.name}"] = pull(cfg, src, via)
    for name, res in results.items():
        entry = status.setdefault(name, {})
        entry["last_attempt"] = now
        entry["error"] = res.get("error", "")
        if res.get("ok"):
            entry["last_success"] = now
            entry["via"] = res.get("via")
        log(f"{name}: {'ok' if res.get('ok') else 'FAILED ' + res.get('error', '')}")
    save_status(cfg, status)
    return results


def load_status(cfg: Config) -> dict:
    try:
        with open(cfg.status_file) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_status(cfg: Config, status: dict):
    os.makedirs(cfg.data_dir, exist_ok=True)
    tmp = cfg.status_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(status, f, indent=1)
    os.replace(tmp, cfg.status_file)
