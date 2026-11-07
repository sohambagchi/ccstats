"""Parser and metrics on hand-built transcripts with hand-computed expected numbers."""
import shutil

import pytest

from ccstats import metrics, store
from ccstats.parse import ParseSettings, bash_executable, parse_file, split_by_day
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
        # Background task finishes later and wakes Claude without a human prompt.
        {"type": "user", "timestamp": ts("10:20:00"), "uuid": "tn", "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": "<task-notification>done</task-notification>"}},
        api_request("10:20:01", "r5"),
        assistant("10:20:04", "m5", [text("it finished")], rid="r5"),
        prompt("10:30:00", uuid="P2", mode="default"),
        api_request("10:30:01", "r6"),
        assistant("10:30:02", "m6", [tool_use("t4", command="make -j8")], rid="r6"),
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

    per_prompt = {r["uuid"]: r for r in p.prompts}
    # P1: [1,15] (API then git) + [15.5,20] + [8:21,8:25.5] + [8:26,8:30] + [20:01,20:04]
    #     = 14 + 4.5 + 4.5 + 4 + 3; the 8-minute question is excluded.
    assert per_prompt["P1"]["claude_s"] == pytest.approx(30.0)
    assert per_prompt["P1"]["api_s"] == pytest.approx(4 + 4.5 + 4 + 4 + 3)
    # P2: [30:01, 30:02 + 600 cap] + [50:03, 50:05]
    assert per_prompt["P2"]["claude_s"] == pytest.approx(601 + 2)
    assert per_prompt["P2"]["api_s"] == pytest.approx(3)


def test_legacy_without_api_request_records(tmp_path, settings):
    rel = "old/claude/projects/-x/legacy.jsonl"
    recs = [
        {"type": "user", "timestamp": ts("09:00:00"), "uuid": "L1", "message": {"role": "user", "content": "hello"}},
        assistant("09:00:10", "m1", [tool_use("t1", command="ls")]),
        tool_result("09:00:12", "t1"),
        assistant("09:00:20", "m2", [text()]),
    ]
    p = parse_file(write(tmp_path, rel, recs), rel, settings)
    assert p.thread["n_prompts"] == 1
    # Each response starts at the record before it: [0,10] + tool [10,12] + [12,20].
    assert p.prompts[0]["claude_s"] == pytest.approx(20)
    assert p.prompts[0]["api_s"] == pytest.approx(18)


def test_midnight_split_in_local_time(tmp_path):
    rel = "h/claude/projects/-x/late.jsonl"
    # Denver is UTC-7 on 2026-11-05, so local midnight is 07:00Z on the 6th.
    recs = [prompt("06:59:50", day="2026-11-06"), api_request("06:59:50", "r", day="2026-11-06"),
            assistant("07:00:10", "m", [text()], rid="r", day="2026-11-06")]
    p = parse_file(write(tmp_path, rel, recs), rel, ParseSettings(tz="America/Denver"))
    days = {b["day"]: b["claude_s"] for b in p.busy}
    assert days == {"2026-11-05": pytest.approx(10), "2026-11-06": pytest.approx(10)}
    # The prompt was sent before midnight; the response arrived after it.
    assert p.prompts[0]["day"] == "2026-11-05" and p.messages[0]["day"] == "2026-11-06"


def test_split_by_day_dst_and_utc():
    from zoneinfo import ZoneInfo
    pieces = list(split_by_day(0, 86400 * 2, ZoneInfo("UTC")))
    assert pieces == [("1970-01-01", 86400), ("1970-01-02", 86400)]


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
    assert row["prompts"] == 2
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
