"""HTTP API: endpoints answer from the store; the client allowlist is enforced."""
import json
import threading
import urllib.request

import pytest

from ccstats import config, server, store
from test_parse import build_archive


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CCSTATS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CCSTATS_CONFIG", str(tmp_path / "none.toml"))
    c = config.load()
    c.timezone = "UTC"
    build_archive(tmp_path / "data" / "archive")
    conn = store.connect(c.db)
    store.refresh(conn, c.archive, c.settings)
    conn.close()
    return c


@pytest.fixture
def base(cfg):
    srv = server.Server(cfg, ("127.0.0.1", 0), server.Refresher(cfg))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def test_endpoints(base):
    assert get(base + "/api/health")["ok"] is True
    rows = get(base + "/api/summary?group=total")["rows"]
    assert rows[0]["prompts"] == 2 and rows[0]["subagents"] == 2
    assert get(base + "/api/summary?group=day&by=host")["rows"][0]["host"] == "spore"
    assert get(base + "/api/summary?since=2027-01-01")["rows"] == []
    assert get(base + "/api/tools")["by_name"][0]["name"] == "Bash"
    assert get(base + "/api/sessions")[0]["session_id"] == "s1"
    assert get(base + "/api/dimensions")["host"] == ["spore"]
    assert "spore/claude" in get(base + "/api/hosts")["archive"]
    with urllib.request.urlopen(base + "/", timeout=5) as r:
        assert b"Claude Code usage" in r.read()
    with urllib.request.urlopen(base + "/push.sh", timeout=5) as r:
        body = r.read().decode()
        assert "@HUB_URL@" not in body and base in body


def test_session_and_cache(base, cfg):
    d = get(base + "/api/session?host=spore&profile=claude&id=s1")
    assert d["session"]["subagents"] == 2 and len(d["threads"]) == 3
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/api/session?host=spore&profile=claude&id=nope")
    assert e.value.code == 404
    with urllib.request.urlopen(base + "/api/sessions", timeout=5) as r:
        etag = r.headers["ETag"]
    req = urllib.request.Request(base + "/api/sessions", headers={"If-None-Match": etag})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 304
    # New data changes the version, so the same URL is answered afresh.
    conn = store.connect(cfg.db)
    conn.execute("INSERT OR REPLACE INTO meta VALUES('data_version', 'next')")
    conn.commit()
    conn.close()
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200 and r.headers["ETag"] != etag


def test_bad_request(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/api/summary?group=year")
    assert e.value.code == 400


def test_refresh_is_accepted(base):
    req = urllib.request.Request(base + "/api/refresh", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 202


def test_acl(monkeypatch):
    monkeypatch.setattr(server, "attached_networks", lambda: ["155.98.68.0/24"])
    acl = server.Acl(["203.0.113.0/24"])
    for ok in ("127.0.0.1", "100.77.231.30", "192.168.1.5", "10.1.2.3", "155.98.68.200", "203.0.113.9",
               "fd7a:115c:a1e0::1", "::ffff:100.85.97.104"):
        assert acl.allows(ok), ok
    for bad in ("8.8.8.8", "155.98.69.1", "2001:db8::1", "::ffff:8.8.8.8", "garbage"):
        assert not acl.allows(bad), bad
