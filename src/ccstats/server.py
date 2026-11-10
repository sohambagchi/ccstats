"""HTTP API and dashboard for the stats database.

Reads are open to clients on loopback, the tailnet, private ranges and the
subnets this machine is directly attached to; everything else gets 403. The
only write is POST /api/refresh, which re-parses changed archive files and is
idempotent, so it needs no token.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import __version__, collect, hosts, metrics, store
from .config import Config

_BASE_NETS = [
    "127.0.0.0/8", "::1/128",
    "100.64.0.0/10", "fd7a:115c:a1e0::/48",           # Tailscale
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # RFC 1918
    "fc00::/7", "fe80::/10",
]
MAX_CONCURRENT = 16
REFRESH_EVERY_S = 300


def attached_networks() -> list[str]:
    """Subnets of this machine's own interfaces, i.e. its local networks."""
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5).stdout
        nets = []
        for iface in json.loads(out or "[]"):
            for a in iface.get("addr_info", []):
                if a.get("local") and a.get("prefixlen") is not None:
                    nets.append(str(ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}").network))
        return nets
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


class Acl:
    def __init__(self, extra: list[str]):
        self.nets = [ipaddress.ip_network(n, strict=False) for n in _BASE_NETS + attached_networks() + extra]

    def allows(self, addr: str) -> bool:
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in n for n in self.nets if n.version == ip.version)


class Refresher:
    """Runs collect-free refreshes of the database: on a timer and on request."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.wanted = threading.Event()
        self.last: dict = {}
        self.reason = "startup"

    def request(self, reason: str = "request"):
        self.reason = reason
        self.wanted.set()

    def loop(self):
        while True:
            asked = self.wanted.wait(REFRESH_EVERY_S)
            self.wanted.clear()
            reason, self.reason = (self.reason if asked else "timer"), "request"
            try:
                with store.compute_lock(self.cfg.data_dir):
                    conn = store.connect(self.cfg.db)
                    try:
                        st = store.refresh(conn, self.cfg.archive, self.cfg.settings,
                                           log=lambda m: print(f"refresh:   {m}", flush=True))
                        self.last = {**st, "at": time.time()}
                    finally:
                        conn.close()
                print(f"refresh ({reason}): {st['parsed']} parsed, {st['unchanged']} unchanged, "
                      f"{st['removed']} removed in {st['seconds']}s", flush=True)
            except Exception:  # keep serving; the next round retries
                traceback.print_exc()
            time.sleep(2)  # coalesce bursts of pushes


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, cfg: Config, addr, refresher: Refresher):
        host = addr[0]
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__(addr, Handler)
        self.cfg = cfg
        self.acl = Acl(cfg.allow)
        self.refresher = refresher
        self.slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def verify_request(self, request, client_address):
        if self.acl.allows(client_address[0]):
            return True
        print(f"{client_address[0]} denied: not loopback, tailnet, private or an attached subnet", flush=True)
        return _deny(request)


def _deny(sock) -> bool:
    try:
        sock.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    except OSError:
        pass
    return False


def _filter(q: dict, tz: ZoneInfo) -> metrics.Filter:
    one = lambda k: (q.get(k) or [None])[0] or None  # noqa: E731
    since, until = one("since"), one("until")
    if one("days") and not since:
        today = datetime.now(tz).date()
        since = (today - timedelta(days=int(one("days")) - 1)).isoformat()
    for d in (since, until):
        if d:
            date.fromisoformat(d)  # reject malformed days early
    return metrics.Filter(since=since, until=until, host=one("host"), profile=one("profile"),
                          project=one("project"), model=one("model"))


ENDPOINTS = {
    "/api/health": "server version, last refresh",
    "/api/summary": "per-bucket stats; group=day|week|month|total, by=host|profile|project|model",
    "/api/tools": "tool calls by name and Bash executable; top=N",
    "/api/models": "tokens by model",
    "/api/sessions": "main sessions with subagents folded in; limit=N",
    "/api/dimensions": "values for host/profile/project/model filters",
    "/api/hosts": "per host/profile coverage, collection status, registered push hosts",
    "POST /api/refresh": "re-parse changed archive files now",
}
FILTER_DOC = "filters on every stats endpoint: days=N or since=YYYY-MM-DD&until=YYYY-MM-DD, host, profile, project, model"


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt, *args):
        # One line per request: client, request line, status (the journal adds the time).
        print(f"{self.client_address[0]} {fmt % args}", flush=True)

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(status, json.dumps(obj, default=str).encode(), "application/json")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.server.slots.acquire(blocking=False):
            return self._json({"error": "busy"}, HTTPStatus.SERVICE_UNAVAILABLE)
        try:
            self._get()
        except (ValueError, KeyError) as e:
            self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
        except Exception as e:  # report, keep serving
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            self.server.slots.release()

    def do_POST(self):
        if urlparse(self.path).path == "/api/refresh":
            self.server.refresher.request(f"POST from {self.client_address[0]}")
            return self._json({"queued": True}, HTTPStatus.ACCEPTED)
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _get(self):
        url = urlparse(self.path)
        path, q = url.path.rstrip("/") or "/", parse_qs(url.query)
        cfg = self.server.cfg
        if path in ("/", "/index.html"):
            return self._send(200, resources.files("ccstats").joinpath("static/index.html").read_bytes(), "text/html; charset=utf-8")
        if path in ("/push.sh", "/cloudlab-setup.sh"):
            body = resources.files("ccstats").joinpath("remote" + path).read_text()
            body = body.replace("@HUB_SSH@", cfg.hub_ssh).replace("@HUB_URL@", self._origin())
            return self._send(200, body.encode(), "text/x-shellscript; charset=utf-8")
        if path == "/api":
            return self._json({"endpoints": ENDPOINTS, "filters": FILTER_DOC})
        if path == "/api/health":
            return self._json({"ok": True, "version": __version__, "timezone": cfg.timezone,
                               "last_refresh": self.server.refresher.last, "computed_at": self._computed_at()})
        if not os.path.exists(cfg.db):
            return self._json({"error": "no database yet; run `ccstats compute`"}, HTTPStatus.SERVICE_UNAVAILABLE)
        conn = store.connect(cfg.db, readonly=True)
        try:
            f = _filter(q, ZoneInfo(cfg.timezone))
            one = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
            if path == "/api/summary":
                group = one("group", "day")
                return self._json({"group": group, "by": one("by"), "filter": f.__dict__,
                                   "rows": metrics.summary(conn, f, group, one("by") or None)})
            if path == "/api/tools":
                return self._json(metrics.tools(conn, f, int(one("top", 25))))
            if path == "/api/models":
                return self._json(metrics.models(conn, f))
            if path == "/api/sessions":
                return self._json(metrics.sessions(conn, f, min(int(one("limit", 100)), 1000)))
            if path == "/api/dimensions":
                return self._json(metrics.dimensions(conn))
            if path == "/api/hosts":
                return self._json({"threads": metrics.hosts(conn), "collect": collect.load_status(cfg),
                                   "push_hosts": hosts.registered(), "archive": archive_mtimes(cfg)})
        finally:
            conn.close()
        self._json({"error": "not found", "endpoints": ENDPOINTS}, HTTPStatus.NOT_FOUND)

    def _origin(self) -> str:
        host = self.headers.get("Host") or f"{self.server.cfg.host}:{self.server.server_address[1]}"
        return f"http://{host}"

    def _computed_at(self):
        try:
            conn = store.connect(self.server.cfg.db, readonly=True)
            row = conn.execute("SELECT v FROM meta WHERE k='computed_at'").fetchone()
            conn.close()
            return float(row[0]) if row else None
        except Exception:
            return None


def archive_mtimes(cfg: Config) -> dict:
    """Newest file time per archive/<host>/<profile>: when data last arrived."""
    out = {}
    if not os.path.isdir(cfg.archive):
        return out
    for host in sorted(os.listdir(cfg.archive)):
        hdir = os.path.join(cfg.archive, host)
        if not os.path.isdir(hdir):
            continue
        for profile in sorted(os.listdir(hdir)):
            newest = 0.0
            for root, _, files in os.walk(os.path.join(hdir, profile)):
                for name in files:
                    try:
                        newest = max(newest, os.stat(os.path.join(root, name)).st_mtime)
                    except OSError:
                        pass
            out[f"{host}/{profile}"] = newest or None
    return out


def serve(cfg: Config, listen: str | None = None):
    listen = listen or cfg.listen
    host, _, port = listen.rpartition(":")
    host = host.strip("[]") or "0.0.0.0"
    refresher = Refresher(cfg)
    srv = Server(cfg, (host, int(port)), refresher)
    threading.Thread(target=refresher.loop, daemon=True).start()
    refresher.request("startup")
    print(f"ccstats {__version__} serving http://{listen} (data {cfg.data_dir})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
