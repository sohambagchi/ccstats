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

    [accounts.claude]                     # a subscription; usage is split by these names
    ids = ["cd7fc0f6-..."]                # Claude account UUIDs (the transcripts' session-owner record)
    weekly_reset = "Wed 23:00 UTC"        # when its weekly limit resets: weekday, time, zone
    weekly_quota_usd = 1273               # estimated weekly limit, in $ at API list prices

    [accounts.claudia]
    ids = ["f4872d10-..."]
    profiles = ["spore/claude"]           # host/profile dirs logged in to it, for transcripts with no UUID

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
class Account:
    """A subscription. A session belongs to the account whose `ids` hold its account UUID, else the one
    listing its `host/profile`, else an account named after its profile."""
    ids: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    weekly_reset: str | None = None      # "Wed 23:00 UTC"; None: weeks are counted back from now
    weekly_quota_usd: float | None = None


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
    accounts: dict[str, Account] = field(default_factory=dict)
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
        accounts={name: Account(ids=list(a.get("ids", [])), profiles=list(a.get("profiles", [])),
                                weekly_reset=a.get("weekly_reset"),
                                weekly_quota_usd=float(a["weekly_quota_usd"]) if "weekly_quota_usd" in a else None)
                  for name, a in (raw.get("accounts") or {}).items()},
        path=path if os.path.exists(path) else None,
    )
