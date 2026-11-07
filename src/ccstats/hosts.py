"""Push-host registry: one restricted line per host in ~/.ssh/authorized_keys.

Each key may only run `rrsync -wo -no-del` rooted at that host's own archive
directory, so a push host can add transcripts under archive/<host>/ and do
nothing else: no shell, no reads, no deletes, no other host's files.
"""
from __future__ import annotations

import os
import re
import shutil

from .config import Config

TAG = "ccstats:"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def _auth_keys() -> str:
    return os.path.expanduser("~/.ssh/authorized_keys")


def _lines() -> list[str]:
    try:
        with open(_auth_keys()) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def _save(lines: list[str]):
    path = _auth_keys()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".ccstats.tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _is_ours(line: str, name: str | None = None) -> bool:
    comment = line.rsplit(" ", 1)[-1] if " " in line else ""
    return comment.startswith(TAG) and (name is None or comment == TAG + name)


def add(cfg: Config, name: str, pubkey: str) -> str:
    if not _NAME.match(name):
        raise SystemExit(f"bad host name {name!r}: use letters, digits, '.', '_' or '-'")
    parts = pubkey.strip().split()
    if len(parts) < 2 or not parts[0].startswith(("ssh-", "ecdsa-", "sk-")):
        raise SystemExit("expected an OpenSSH public key like 'ssh-ed25519 AAAA... comment'")
    rrsync = shutil.which("rrsync") or "/usr/bin/rrsync"
    root = os.path.join(cfg.archive, name)
    os.makedirs(root, exist_ok=True)
    line = f'command="{rrsync} -wo -no-del {root}",restrict {parts[0]} {parts[1]} {TAG}{name}'
    lines = [ln for ln in _lines() if not _is_ours(ln, name)] + [line]
    _save(lines)
    return line


def remove(name: str) -> bool:
    lines = _lines()
    kept = [ln for ln in lines if not _is_ours(ln, name)]
    if len(kept) == len(lines):
        return False
    _save(kept)
    return True


def registered() -> list[str]:
    return [ln.rsplit(" ", 1)[-1][len(TAG):] for ln in _lines() if _is_ours(ln)]
