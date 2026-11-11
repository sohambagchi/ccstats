"""Parser and metrics on hand-built transcripts with hand-computed expected numbers."""
import shutil

import pytest

from ccstats import metrics, store
from ccstats.parse import ParseSettings, bash_executable, parse_file, split_by_day, split_by_hour
from transcripts import api_request, assistant, prompt, text, tool_result, tool_use, ts, usage, write

MAIN = "spore/claude/projects/-home-u-dev-proj/s1.jsonl"


def main_session():
    """Two prompts exercising every Claude-time rule; expected values in the tests below."""
    return [
        prompt("10:00:00", uuid="P1"),
        api_request("10:00:01", "r1"),
        assistant("10:00:03", "m1", [text("thinking aloud")], rid="r1"),
        assistant("10:00:05", "m1", [tool_use("t1", command="cd x && git status")], rid="r1"),  # same message, 2nd block
        tool_result("10:00:15", "t1"),
        api_request("10:00:15.500", "r2"),
        assistant("10:00:20", "m2", [tool_use("t2", "AskUserQuestion", questions=[])], rid="r2"),
        tool_result("10:08:20", "t2"),                       # 8 minutes of the human deciding
        api_request("10:08:21", "r3"),
        assistant("10:08:25", "m3", [tool_use("t3", command="sleep 100", run_in_background=True)], rid="r3"),
        tool_result("10:08:25.500", "t3", tur={"backgroundTaskId": "b1", "stdout": ""}),
        api_request("10:08:26", "r4"),
        assistant("10:08:30", "m4", [text("started it")], rid="r4"),
        # Claude Code's own timer for the turn: the prompt to control returning, question included.
        {"type": "system", "subtype": "turn_duration", "timestamp": ts("10:08:30.200"), "durationMs": 510200},
        # Background task finishes later and wakes Claude without a human prompt.
        {"type": "user", "timestamp": ts("10:20:00"), "uuid": "tn", "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
        api_request("10:20:01", "r5"),
        assistant("10:20:04", "m5", [text("it finished")], rid="r5"),
        prompt("10:30:00", uuid="P2", mode="default"),
        api_request("10:30:01", "r6"),
        {**assistant("10:30:02", "m6", [tool_use("t4", command="make -j8")], rid="r6"),
         "effort": "xhigh", "attributionSkill": "docky"},
        # Approved by a human after a long wait: capped at the approval cap.
        tool_result("10:50:02", "t4", is_error=True, pd={"decision": "accept", "source": "user_temporary"}),
        api_request("10:50:03", "r7"),
        assistant("10:50:05", "m7", [text("done")], rid="r7"),
        {"type": "assistant", "timestamp": ts("10:50:06"), "isApiErrorMessage": True,
         "message": {"id": "synthetic-1", "model": "<synthetic>", "content": [text("error")], "usage": usage(out=0)}},
        {"type": "custom-title", "customTitle": "my session"},
        {"type": "session-owner", "accountUuid": "acct-1"},
    ]


@pytest.fixture
def settings():
    return ParseSettings(tz="UTC", approval_cap_s=600)


def test_main_session(tmp_path, settings):
    path = write(tmp_path, MAIN, main_session(), tail='{"type":"assistant","trunc')
    p = parse_file(path, MAIN, settings)

    assert p.thread["host"] == "spore" and p.thread["profile"] == "claude" and p.thread["session_id"] == "s1"
    assert p.thread["project"] == "proj" and p.thread["title"] == "my session" and p.thread["account"] == "acct-1"
    assert p.thread["n_prompts"] == 2

    # Seven real API responses; m1's two blocks count once, the synthetic error not at all.
    assert sorted(m["message_id"] for m in p.messages) == [f"m{i}" for i in range(1, 8)]
    assert sum(m["output"] for m in p.messages) == 700
    assert sum(m["cache_read"] for m in p.messages) == 7000

    tools = {t["tool_use_id"]: t for t in p.tools}
    assert tools["t1"]["exe"] == "git"
    assert tools["t2"]["is_decision"] == 1
    assert tools["t3"]["is_background"] == 1 and tools["t3"]["exe"] == "sleep"
    assert tools["t4"]["human_approved"] == 1 and tools["t4"]["is_error"] == 1 and tools["t4"]["exe"] == "make"

    turns = {r["uuid"]: r for r in p.turns}
    assert {u: r["origin"] for u, r in turns.items()} == {"P1": "human", "tn": "task-notification", "P2": "human"}
    assert p.thread["n_turns"] == 3
    # P1: [1,15] (API then git) + [15.5,20] + [8:21,8:25.5] + [8:26,8:30]
    #     = 14 + 4.5 + 4.5 + 4; the 8-minute question is excluded.
    assert turns["P1"]["claude_s"] == pytest.approx(27.0)
    assert turns["P1"]["api_s"] == pytest.approx(4 + 4.5 + 4 + 4)
    assert turns["P1"]["cc_turn_s"] == pytest.approx(510.2)
    # The background task's notification starts its own turn: [20:01, 20:04].
    assert turns["tn"]["claude_s"] == pytest.approx(3) and turns["tn"]["cc_turn_s"] is None
    # P2: [30:01, 30:02 + 600 cap] + [50:03, 50:05]
    assert turns["P2"]["claude_s"] == pytest.approx(601 + 2)
    assert turns["P2"]["api_s"] == pytest.approx(3)

    msgs = {m["message_id"]: m for m in p.messages}
    assert (msgs["m1"]["turn"], msgs["m5"]["turn"], msgs["m6"]["turn"]) == ("P1", "tn", "P2")
    assert (msgs["m6"]["effort"], msgs["m6"]["skill"]) == ("xhigh", "docky") and msgs["m1"]["effort"] is None
    assert tools["t4"]["turn"] == "P2"
    # Busy time is kept per turn, local day and hour.
    assert {(b["turn"], b["hour"]) for b in p.busy} == {("P1", 10), ("tn", 10), ("P2", 10)}


def test_legacy_without_api_request_records(tmp_path, settings):
    rel = "old/claude/projects/-x/legacy.jsonl"
    recs = [
        {"type": "user", "timestamp": ts("09:00:00"), "uuid": "L1", "message": {"role": "user", "content": "hello"}},
        assistant("09:00:10", "m1", [tool_use("t1", command="ls")]),
        tool_result("09:00:12", "t1"),
        assistant("09:00:20", "m2", [text()]),
        {"type": "user", "timestamp": ts("09:30:00"), "uuid": "N1",
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
    ]
    p = parse_file(write(tmp_path, rel, recs), rel, settings)
    assert p.thread["n_prompts"] == 1
    assert [(t["uuid"], t["origin"]) for t in p.turns] == [("L1", "human"), ("N1", "task-notification")]
    # Each response starts at the record before it: [0,10] + tool [10,12] + [12,20].
    assert p.turns[0]["claude_s"] == pytest.approx(20)
    assert p.turns[0]["api_s"] == pytest.approx(18)


def test_midnight_split_in_local_time(tmp_path):
    rel = "h/claude/projects/-x/late.jsonl"
    # Denver is UTC-7 on 2026-11-05, so local midnight is 07:00Z on the 6th.
    recs = [prompt("06:59:50", day="2026-11-06"), api_request("06:59:50", "r", day="2026-11-06"),
            assistant("07:00:10", "m", [text()], rid="r", day="2026-11-06")]
    p = parse_file(write(tmp_path, rel, recs), rel, ParseSettings(tz="America/Denver"))
    cells = {(b["day"], b["hour"]): b["claude_s"] for b in p.busy}
    assert cells == {("2026-11-05", 23): pytest.approx(10), ("2026-11-06", 0): pytest.approx(10)}
    # The prompt was sent before midnight; the response arrived after it.
    assert p.turns[0]["day"] == "2026-11-05" and p.messages[0]["day"] == "2026-11-06"


def test_split_by_day_dst_and_utc():
    from zoneinfo import ZoneInfo
    pieces = list(split_by_day(0, 86400 * 2, ZoneInfo("UTC")))
    assert pieces == [("1970-01-01", 86400), ("1970-01-02", 86400)]


def test_split_by_hour_follows_local_hour_marks():
    from zoneinfo import ZoneInfo
    # Kolkata is UTC+5:30, so local hours turn at :30 UTC.
    assert list(split_by_hour(0, 3600, ZoneInfo("Asia/Kolkata"))) == [("1970-01-01", 5, 1800), ("1970-01-01", 6, 1800)]
    # Denver falls back on 2026-11-01 at 08:00Z: 01:00 local happens twice, as two separate hours.
    start = 1793520000 - 1800  # 07:30Z = 01:30 MDT
    pieces = list(split_by_hour(start, start + 7200, ZoneInfo("America/Denver")))
    assert [(h, s) for _, h, s in pieces] == [(1, 1800), (1, 3600), (2, 1800)]


def _subagent(clock_start, clock_end, agent_id, agent_type, depth):
    rel = f"spore/claude/projects/-home-u-dev-proj/s1/subagents/agent-{agent_id}.jsonl"
    recs = [{"type": "user", "timestamp": ts(clock_start), "uuid": f"sp-{agent_id}", "isSidechain": True,
             "agentId": agent_id, "message": {"role": "user", "content": "explore things"}},
            api_request(clock_start, f"r-{agent_id}"),
            assistant(clock_end, f"m-{agent_id}", [text()], rid=f"r-{agent_id}", u=usage(out=40))]
    meta = {"agentType": agent_type, "spawnDepth": depth, "toolUseId": f"tu-{agent_id}", "description": "d"}
    return rel, recs, meta


def build_archive(root):
    write(root, MAIN, main_session())
    for args in (("10:00:00", "10:05:00", "a1", "Explore", 1), ("10:03:00", "10:10:00", "b2", "Plan", 2)):
        rel, recs, meta = _subagent(*args)
        write(root, rel, recs, meta=meta)


def test_store_and_summary(tmp_path, settings):
    archive = tmp_path / "archive"
    build_archive(archive)
    # A forked copy of the session repeats its records; nothing may count twice.
    fork = archive / "spore/claude/projects/-home-u-dev-proj/s2-fork.jsonl"
    shutil.copy(archive / MAIN, fork)

    conn = store.connect(str(tmp_path / "db.sqlite"))
    assert store.refresh(conn, str(archive), settings)["parsed"] == 4
    assert store.refresh(conn, str(archive), settings)["unchanged"] == 4

    [row] = metrics.summary(conn, metrics.Filter(), "day")
    assert row["bucket"] == "2026-11-05"
    # The fork only repeats s1's records, so it adds no activity, prompts, tokens or time.
    assert row["sessions"] == 1
    assert row["prompts"] == 2 and row["turns"] == 3
    assert row["tokens"]["main"]["output"] == 700
    assert row["tokens"]["sub"]["output"] == 80
    assert row["claude_s"] == pytest.approx(633)
    assert row["agent_s"] == pytest.approx(300 + 420)
    assert row["subagents"] == 2 and row["peak_concurrent_subagents"] == 2
    assert row["max_spawn_depth"] == 2 and row["agent_types"] == {"Explore": 1, "Plan": 1}
    assert row["tool_calls"] == 4 and row["tool_errors"] == 1 and row["tool_background"] == 1

    [week] = metrics.summary(conn, metrics.Filter(), "week")
    assert week["bucket"] == "2026-W45" and week["claude_s"] == pytest.approx(633)
    assert metrics.summary(conn, metrics.Filter(since="2026-11-06"), "day") == []

    by_model = metrics.summary(conn, metrics.Filter(), "total", by="model")
    assert [r["model"] for r in by_model] == ["claude-opus-5-5"]
    by_origin = {r["origin"]: r for r in metrics.summary(conn, metrics.Filter(), "total", by="origin")}
    assert by_origin["human"]["claude_s"] == pytest.approx(630) and by_origin["human"]["prompts"] == 2
    assert by_origin["task-notification"]["claude_s"] == pytest.approx(3) and by_origin["task-notification"]["turns"] == 1
    # These subagents' spawning calls are not in the transcript: no turn to credit them to.
    assert by_origin["unknown"]["agent_s"] == pytest.approx(720) and by_origin["unknown"]["subagents"] == 2

    t = metrics.tools(conn, metrics.Filter())
    assert {r["name"]: r["calls"] for r in t["by_name"]} == {"Bash": 3, "AskUserQuestion": 1}
    assert {r["exe"] for r in t["bash"]} == {"git", "sleep", "make"}

    sessions = metrics.sessions(conn, metrics.Filter())
    s1 = next(s for s in sessions if s["session_id"] == "s1")
    assert s1["subagents"] == 2 and s1["agent_s"] == pytest.approx(720) and s1["tokens"]["output"] == 780


def test_changed_file_is_reparsed(tmp_path, settings):
    archive = tmp_path / "archive"
    path = write(archive, MAIN, main_session()[:5])
    conn = store.connect(str(tmp_path / "db.sqlite"))
    store.refresh(conn, str(archive), settings)
    before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    write(archive, MAIN, main_session())
    assert store.refresh(conn, str(archive), settings)["parsed"] == 1
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 7 > before
    assert path


@pytest.mark.parametrize("cmd,exe", [
    ("git status", "git"),
    ("cd ~/x && cargo build --release", "cargo"),
    ("FOO=1 sudo -E make -j4", "make"),
    ("timeout 5 curl -s x | jq .", "curl"),
    ("for h in a b; do ssh $h ls; done", "ssh"),
    ("if [ -f x ]; then pytest -q; fi", "pytest"),
    ("(cd a && ./run.sh)", "run.sh"),
    ("python3 - <<'EOF'\nimport os\nEOF", "python3"),
    ("/usr/bin/git log", "git"),
    ("echo hi", "echo"),
])
def test_bash_executable(cmd, exe):
    assert bash_executable(cmd) == exe


def test_session_rollup_and_agent_tree(tmp_path, settings):
    archive = tmp_path / "archive"
    build_archive(archive)
    # Agent b2 was spawned by agent a1's Agent call, not by the main thread.
    rel, recs, meta = _subagent("10:00:00", "10:05:00", "a1", "Explore", 1)
    recs.insert(2, assistant("10:02:59", "m-a1-spawn", [tool_use("tu-b2", "Agent", prompt="nested")], u=usage(out=5)))
    write(archive, rel, recs, meta={**meta, "isFork": True, "requestShape": "background"})

    conn = store.connect(str(tmp_path / "db.sqlite"))
    assert store.refresh(conn, str(archive), settings)["sessions"] == 1
    [s] = metrics.sessions(conn, metrics.Filter())
    assert s["subagents"] == 2 and s["forks"] == 1 and s["background_agents"] == 1
    assert s["agent_types"] == {"Explore": 1, "Plan": 1} and s["depth_counts"] == {"1": 1, "2": 1}
    assert s["tool_calls"] == 5 and s["tokens"]["output"] == 785

    d = metrics.session_detail(conn, "spore", "claude", "s1")
    by_id = {t["id"]: t for t in d["threads"]}
    assert by_id["b2"]["parent"] == "a1" and by_id["a1"]["parent"] == "main" and by_id["main"]["parent"] is None
    assert by_id["a1"]["agent_calls"] == 1 and by_id["main"]["claude_s"] == pytest.approx(633)
    assert by_id["b2"]["spans"] == [[ts_epoch("10:03:00"), ts_epoch("10:10:00")]]
    assert [p["ts"] for p in d["prompts"]] == [ts_epoch("10:00:00"), ts_epoch("10:30:00")]
    assert metrics.session_detail(conn, "spore", "claude", "nope") is None

    # Nothing changed: no session is recomputed.
    assert store.refresh(conn, str(archive), settings)["sessions"] == 0


def test_turns_carry_their_subagents(tmp_path, settings):
    """A turn's stats include the subagents it spawned, nested ones too, and their cost and tools."""
    archive = tmp_path / "archive"
    write(archive, MAIN, main_session() + [
        prompt("11:00:00", uuid="P3"), api_request("11:00:01", "r8"),
        assistant("11:00:02", "m8", [tool_use("tu-c3", "Agent", prompt="go")], rid="r8"),
        tool_result("11:04:00", "tu-c3"),
        {"type": "system", "subtype": "turn_duration", "timestamp": ts("11:04:00"), "durationMs": 240000},
    ])
    rel, recs, meta = _subagent("11:00:03", "11:03:00", "c3", "general-purpose", 1)
    recs.insert(2, assistant("11:01:00", "m-c3-spawn", [tool_use("tu-d4", "Agent", prompt="deeper")], u=usage(out=5)))
    write(archive, rel, recs, meta=meta)
    rel, recs, meta = _subagent("11:01:01", "11:02:01", "d4", "Explore", 2)
    write(archive, rel, recs, meta=meta)
    conn = store.connect(str(tmp_path / "db.sqlite"))
    store.refresh(conn, str(archive), settings)

    [p3] = metrics.turns(conn, metrics.Filter(), sort="agent", limit=1)
    assert p3["uuid"] == "P3" and p3["origin"] == "human" and p3["subagents"] == 2
    assert p3["claude_s"] == pytest.approx(239) and p3["cc_turn_s"] == pytest.approx(240)
    assert p3["agent_s"] == pytest.approx(177 + 60) and p3["tool_calls"] == 2
    assert p3["usd"] == pytest.approx(conn.execute(
        "SELECT SUM(usd) FROM u_messages WHERE message_id IN ('m8', 'm-c3', 'm-c3-spawn', 'm-d4')").fetchone()[0])
    assert [t["uuid"] for t in metrics.turns(conn, metrics.Filter(), origin="autonomous")] == ["tn"]

    check = metrics.timecheck(conn, metrics.Filter())
    assert check["turns"] == 4 and check["timed"] == 2
    assert check["ratio_median"] == pytest.approx(239 / 240, abs=1e-3)

    hm = metrics.heatmap(conn, metrics.Filter())
    cells = {(c["weekday"], c["hour"]): c for c in hm["cells"]}
    assert cells[(3, 11)]["agent_s"] == pytest.approx(237) and cells[(3, 11)]["prompts"] == 1  # a Thursday
    assert cells[(3, 10)]["turns"] == 3 and hm["weekday_counts"][3] == 1

    efforts = {r["effort"]: r["api_calls"] for r in metrics.cost(conn, metrics.Filter(), "effort")}
    assert efforts == {"xhigh": 1, None: 10}
    skills = {r["skill"] for r in metrics.cost(conn, metrics.Filter(), "skill")}
    assert skills == {"docky", None}
    d = metrics.session_detail(conn, "spore", "claude", "s1")
    assert [t["origin"] for t in d["turns"]] == ["human", "task-notification", "human", "human"]
    assert len(d["prompts"]) == 3


def test_dedup_winner_moves_incrementally(tmp_path, settings):
    """A copy sorting before the original takes over its records; the original's rollup follows."""
    archive = tmp_path / "archive"
    write(archive, MAIN, main_session())
    conn = store.connect(str(tmp_path / "db.sqlite"))
    store.refresh(conn, str(archive), settings)
    early = "spore/claude/projects/-home-u-dev-proj/a0-copy.jsonl"
    shutil.copy(archive / MAIN, archive / early)
    st = store.refresh(conn, str(archive), settings)
    assert st["parsed"] == 1 and st["sessions"] == 2
    out = {s["session_id"]: s["tokens"]["output"] for s in metrics.sessions(conn, metrics.Filter())}
    assert out == {"a0-copy": 700, "s1": 0}
    (archive / early).unlink()
    store.refresh(conn, str(archive), settings)
    assert {s["session_id"]: s["tokens"]["output"] for s in metrics.sessions(conn, metrics.Filter())} == {"s1": 700}


def test_old_schema_is_rebuilt(tmp_path, settings):
    db = str(tmp_path / "db.sqlite")
    conn = store.connect(db)
    conn.execute("UPDATE meta SET v = '1' WHERE k = 'schema'")
    conn.execute("CREATE TABLE leftover(x)")
    conn.commit()
    conn.close()
    conn = store.connect(db)
    assert conn.execute("SELECT v FROM meta WHERE k = 'schema'").fetchone()[0] == str(store.SCHEMA_VERSION)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'leftover'").fetchone() is None


def ts_epoch(clock):
    from ccstats.parse import iso_ts
    return iso_ts(ts(clock))


def test_pricing():
    from ccstats import pricing
    # Opus 5.5: $4 in, $20 out, $0.20 cache read; writes at 1.25x (5m) or 2x (1h) of input.
    assert pricing.usd("claude-opus-5-5", 1_000_000, 0, 0, 0, 0) == pytest.approx(4.0)
    assert pricing.usd("claude-opus-5-5", 0, 1_000_000, 1_000_000, 0, 0) == pytest.approx(20.2)
    assert pricing.usd("claude-opus-5-5", 0, 0, 0, 2_000_000, 1_000_000) == pytest.approx(5.0 + 8.0)
    # Longest prefix wins; date suffixes and [1m] tags are ignored.
    assert pricing.usd("claude-opus-5[1m]", 1_000_000) == pytest.approx(5.0)
    assert pricing.usd("claude-haiku-4-5-20251001", 1_000_000) == pytest.approx(1.0)
    assert pricing.usd("claude-opus-4-1-20250805", 1_000_000) == pytest.approx(15.0)
    # Haiku 5.5 is billed 5x above 100K prompt tokens.
    assert pricing.usd("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.1)
    assert pricing.usd("<synthetic>", 1_000_000) == 0 and not pricing.known("?")


def test_costs_roll_up_and_reprice(tmp_path, settings, monkeypatch):
    from ccstats import pricing
    archive = tmp_path / "archive"
    build_archive(archive)
    conn = store.connect(str(tmp_path / "db.sqlite"))
    store.refresh(conn, str(archive), settings)
    [row] = metrics.summary(conn, metrics.Filter(), "total")
    assert row["cost"]["main"] > 0 and row["cost"]["sub"] > 0
    assert row["cost"]["total"] == pytest.approx(row["cost"]["main"] + row["cost"]["sub"])
    [m] = metrics.models(conn, metrics.Filter())
    assert m["priced"] and m["usd"] == pytest.approx(row["cost"]["total"])
    s1 = next(s for s in metrics.sessions(conn, metrics.Filter()) if s["session_id"] == "s1")
    assert s1["usd"] == pytest.approx(row["cost"]["total"])
    d = metrics.session_detail(conn, "spore", "claude", "s1")
    assert sum(t["usd"] for t in d["threads"]) == pytest.approx(s1["usd"])

    # A price change re-prices stored rows and rollups without re-parsing anything.
    monkeypatch.setitem(pricing.PRICES, "claude-opus-5-5", tuple(2 * p for p in pricing.PRICES["claude-opus-5-5"]))
    monkeypatch.setattr(pricing, "VERSION", "test")
    st = store.refresh(conn, str(archive), settings)
    assert st["parsed"] == 0 and st["repriced_sessions"] >= 1
    s1b = next(s for s in metrics.sessions(conn, metrics.Filter()) if s["session_id"] == "s1")
    assert s1b["usd"] == pytest.approx(2 * s1["usd"])


def test_subagents_count_toward_their_sessions_project(tmp_path, settings):
    archive = tmp_path / "archive"
    # A subagent that ran in a worktree, arriving before its main transcript.
    rel, recs, meta = _subagent("10:00:00", "10:05:00", "w1", "general-purpose", 1)
    for r in recs:
        r["cwd"] = "/home/u/dev/proj/.claude/worktrees/agent-w1"
    write(archive, rel, recs, meta=meta)
    conn = store.connect(str(tmp_path / "db.sqlite"))
    store.refresh(conn, str(archive), settings)
    assert metrics.dimensions(conn)["project"] == ["agent-w1"]  # no main thread yet: its own directory

    build_archive(archive)
    store.refresh(conn, str(archive), settings)
    assert metrics.dimensions(conn)["project"] == ["proj"]
    cwd = conn.execute("SELECT cwd FROM threads WHERE agent_id = 'w1'").fetchone()[0]
    assert cwd.endswith("/.claude/worktrees/agent-w1")  # where it really ran is kept
    # Filtering by the project now counts the same work the sessions list shows.
    [whole] = metrics.summary(conn, metrics.Filter(), "total")
    [proj] = metrics.summary(conn, metrics.Filter(project="proj"), "total")
    assert proj["subagents"] == whole["subagents"] == 3
    assert proj["cost"]["total"] == pytest.approx(whole["cost"]["total"])
    [s1] = metrics.sessions(conn, metrics.Filter(project="proj"))
    assert s1["usd"] == pytest.approx(proj["cost"]["total"])

    # A database rolled up by an older version is fixed on the next refresh, without re-parsing.
    conn.execute("UPDATE threads SET project = 'stale' WHERE is_subagent = 1")
    conn.execute("UPDATE meta SET v = '1' WHERE k = 'rollup'")
    conn.commit()
    before = store.data_version(conn)
    st = store.refresh(conn, str(archive), settings)
    assert st["parsed"] == 0 and st["sessions"] == 1
    assert metrics.dimensions(conn)["project"] == ["proj"]
    assert store.data_version(conn) != before
