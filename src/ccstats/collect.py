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


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _stat(out: str, label: str) -> int:
    for line in out.splitlines():
        if line.startswith(label + ":"):
            digits = "".join(ch for ch in line.split(":", 1)[1].split("(")[0] if ch.isdigit())
            return int(digits or 0)
    return 0


def _rsync(src: str, dst: str, ssh: bool) -> dict:
    """Copy src into dst; report what moved: files, their size, bytes on the wire, seconds."""
    os.makedirs(dst, exist_ok=True)
    cmd = ["rsync", "-a", "--timeout=120", "--stats", "--out-format=%n", *RSYNC_FILTERS]
    if ssh:
        # Transcripts are JSON text and compress about 5x.
        cmd += ["-z", "-e", SSH_OPTS]
    cmd += [src.rstrip("/") + "/", dst.rstrip("/") + "/"]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout
    # --out-format lines come before the --stats block, which opens with "Number of files:".
    names = []
    for ln in out.splitlines():
        if ln.startswith("Number of files:"):
            break
        if ln and not ln.endswith("/"):
            names.append(ln)
    # 23/24 = some files vanished or were unreadable mid-copy; the rest arrived.
    ok = p.returncode in (0, 23, 24)
    return {
        "ok": ok,
        "error": "" if ok else _error(p.stderr, p.returncode),
        "warning": "some files vanished or were unreadable mid-copy" if p.returncode in (23, 24) else "",
        "files": names,
        "size": _stat(out, "Total transferred file size"),
        "wire": _stat(out, "Total bytes received") if src.count(":") else _stat(out, "Total bytes sent"),
        "seconds": round(time.time() - t0, 1),
    }


def _error(stderr: str, code: int) -> str:
    """The useful part of a failed rsync's stderr: ssh's own complaint comes before rsync's summary."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    cause = [ln for ln in lines if not ln.startswith("rsync")]
    return "; ".join(cause[-2:] or lines[-1:]) or f"rsync exit {code}"


def _merge(results: list[dict]) -> dict:
    return {
        "ok": all(r["ok"] for r in results),
        "error": "; ".join(r["error"] for r in results if r["error"]),
        "warning": "; ".join(r["warning"] for r in results if r["warning"]),
        "files": [f for r in results for f in r["files"]],
        "size": sum(r["size"] for r in results),
        "wire": sum(r["wire"] for r in results),
        "seconds": round(sum(r["seconds"] for r in results), 1),
    }


def collect_local(cfg: Config, log=print) -> dict:
    """Snapshot this machine's profiles into archive/<host>/<profile>/projects."""
    out = {}
    for profile, cfg_dir in cfg.profiles.items():
        src = os.path.join(os.path.expanduser(cfg_dir), "projects")
        if not os.path.isdir(src):
            log(f"{cfg.host}/{profile}: skipped, no {src}")
            continue
        dst = os.path.join(cfg.archive, cfg.host, profile, "projects")
        log(f"{cfg.host}/{profile}: copying {src}/ -> {dst}/")
        out[f"{cfg.host}/{profile}"] = _rsync(src, dst, ssh=False)
    return out


def pull(cfg: Config, src: Pull, via: str | None = None, log=print) -> dict:
    """Fetch one pull source, trying its ssh targets in order until one answers."""
    targets = [via] if via else src.ssh
    last_err = "no ssh targets"
    for target in targets:
        if src.kind == "cloudlab":
            log(f"pull:{src.name}: rsync {target}:{src.path} -> {cfg.archive}/")
            res = _rsync(f"{target}:{src.path}", cfg.archive, ssh=True)
        else:
            parts = []
            for profile, cfg_dir in src.profiles.items():
                remote = cfg_dir.replace("~/", "", 1) if cfg_dir.startswith("~/") else cfg_dir
                dst = os.path.join(cfg.archive, src.name, profile, "projects")
                log(f"pull:{src.name}: rsync {target}:{remote}/projects -> {dst}/")
                r = _rsync(f"{target}:{remote}/projects", dst, ssh=True)
                # A host may lack a profile; only a failure to reach it counts.
                if not r["ok"] and "No such file" in r["error"]:
                    log(f"pull:{src.name}: {target} has no {remote}/projects, skipped")
                    r = {**r, "ok": True, "error": ""}
                parts.append(r)
            res = _merge(parts)
        if res["ok"]:
            return {**res, "via": target}
        if len(targets) > 1:
            log(f"pull:{src.name}: {target} failed, trying the next target: {res['error']}")
        last_err = f"{target}: {res['error']}"
    return {"ok": False, "via": None, "error": last_err, "warning": "", "files": [], "size": 0, "wire": 0, "seconds": 0}


def run(cfg: Config, only: str | None = None, via: str | None = None, log=print, verbose: bool = True) -> dict:
    """Run the local snapshot and the pulls (or just `only`), log each, and record status.json."""
    quiet = (lambda _msg: None) if not verbose else log
    status = load_status(cfg)
    now = time.time()
    results: dict[str, dict] = {}
    if only is None or only == cfg.host:
        results.update(collect_local(cfg, log=quiet))
    for src in cfg.pulls:
        if only is None or only == src.name:
            results[f"pull:{src.name}"] = pull(cfg, src, via, log=quiet)
    for name, res in results.items():
        entry = status.setdefault(name, {})
        entry["last_attempt"] = now
        entry["error"] = res.get("error", "")
        if res.get("ok"):
            entry["last_success"] = now
            entry["via"] = res.get("via")
            n = len(res["files"])
            via_s = f" via {res['via']}" if res.get("via") else ""
            line = (f"{name}: ok{via_s}, {n} file{'s' if n != 1 else ''} updated"
                    + (f" ({_human(res['size'])}, {_human(res['wire'])} transferred)" if n else "")
                    + f" in {res['seconds']}s")
            log(line + (f" [warning: {res['warning']}]" if res.get("warning") else ""))
            if verbose:
                for f in res["files"]:
                    log(f"  + {f}")
        else:
            log(f"{name}: FAILED {res.get('error', '')}")
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
