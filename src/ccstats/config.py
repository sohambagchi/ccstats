"""Settings, read from `$CCSTATS_CONFIG` or `~/.config/ccstats/config.toml` (all keys optional).

    data_dir = "~/.local/share/ccstats"   # archive/ and ccstats.db live here
    listen = "0.0.0.0:8420"
    timezone = "America/Denver"           # days and weeks are local to this zone
    approval_cap_minutes = 10             # longest a human-approved tool call counts as Claude time
    host = "spore"                        # this machine's name in the archive
    hub_ssh = "soham@spore"               # what push hosts ssh to
    allow = ["10.0.0.0/8"]                # extra client networks for the web server

    [profiles]                            # Claude Code config dirs on this machine
    claude = "~/.claude"
    claudia = "~/.claudia"

    [[pull]]                              # archives the hub fetches over ssh
    name = "utah"
    kind = "cloudlab"                     # remote path holds <node>/<profile>/projects
    ssh = ["sohamb@node0.exp.proj.utah.cloudlab.us"]   # tried in order
    path = "/proj/<project>/ccstats/"

    [[pull]]
    name = "buckwheat"
    kind = "host"                         # remote path holds Claude Code config dirs
    ssh = ["buckwheat"]
    profiles = { claude = "~/.claude", claudia = "~/.claudia" }
"""
from __future__ import annotations

import os
import socket
import tomllib
from dataclasses import dataclass, field

from .parse import ParseSettings


def _system_tz() -> str:
    if os.environ.get("TZ"):
        return os.environ["TZ"].lstrip(":")
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "UTC"


@dataclass
class Pull:
    name: str
    kind: str
    ssh: list[str]
    path: str = ""
    profiles: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    data_dir: str
    listen: str
    timezone: str
    approval_cap_minutes: float
    host: str
    hub_ssh: str
    allow: list[str]
    profiles: dict[str, str]
    pulls: list[Pull]
    path: str | None = None

    @property
    def archive(self) -> str:
        return os.path.join(self.data_dir, "archive")

    @property
    def db(self) -> str:
        return os.path.join(self.data_dir, "ccstats.db")

    @property
    def status_file(self) -> str:
        return os.path.join(self.data_dir, "status.json")

    @property
    def settings(self) -> ParseSettings:
        return ParseSettings(tz=self.timezone, approval_cap_s=self.approval_cap_minutes * 60)


def load(path: str | None = None) -> Config:
    path = path or os.environ.get("CCSTATS_CONFIG") or os.path.expanduser("~/.config/ccstats/config.toml")
    raw: dict = {}
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    host = raw.get("host") or socket.gethostname().split(".")[0]
    data_dir = os.environ.get("CCSTATS_DATA") or raw.get("data_dir") or "~/.local/share/ccstats"
    pulls = [Pull(name=p["name"], kind=p.get("kind", "host"),
                  ssh=[p["ssh"]] if isinstance(p.get("ssh"), str) else list(p.get("ssh", [])),
                  path=p.get("path", ""), profiles=dict(p.get("profiles") or {"claude": "~/.claude", "claudia": "~/.claudia"}))
             for p in raw.get("pull", [])]
    return Config(
        data_dir=os.path.expanduser(data_dir),
        listen=raw.get("listen", "0.0.0.0:8420"),
        timezone=raw.get("timezone") or _system_tz(),
        approval_cap_minutes=float(raw.get("approval_cap_minutes", 10)),
        host=host,
        hub_ssh=raw.get("hub_ssh") or f"{os.environ.get('USER', 'user')}@{host}",
        allow=list(raw.get("allow", [])),
        profiles=dict(raw.get("profiles") or {"claude": "~/.claude", "claudia": "~/.claudia"}),
        pulls=pulls,
        path=path if os.path.exists(path) else None,
    )
