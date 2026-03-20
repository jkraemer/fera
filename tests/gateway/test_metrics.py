import time

import pytest
from fera.gateway.metrics import MetricsCollector


def test_create_db(tmp_path):
    db_path = tmp_path / "metrics.db"
    mc = MetricsCollector(db_path)
    assert db_path.exists()
    mc.close()


@pytest.mark.asyncio
async def test_record_agent_done(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    event = {
        "event": "agent.done",
        "session": "main/default",
        "data": {
            "input_tokens": 5000,
            "output_tokens": 1500,
            "cost_usd": 0.04,
            "model": "claude-sonnet-4-20250514",
        },
    }
    await mc.record(event)

    rows = mc._query_raw("SELECT metric, value, session FROM metrics ORDER BY metric")
    metrics = {r[0]: r[1] for r in rows}
    assert metrics["cost"] == 0.04
    assert metrics["tokens_in"] == 5000
    assert metrics["tokens_out"] == 1500
    assert metrics["turn"] == 1
    assert rows[0][2] == "main/default"
    mc.close()


@pytest.mark.asyncio
async def test_record_ignores_system_session(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    event = {"event": "agent.done", "session": "$system", "data": {"input_tokens": 100}}
    await mc.record(event)
    rows = mc._query_raw("SELECT COUNT(*) FROM metrics")
    assert rows[0][0] == 0
    mc.close()


@pytest.mark.asyncio
async def test_record_compact(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    event = {"event": "agent.compact", "session": "main/default", "data": {}}
    await mc.record(event)
    rows = mc._query_raw("SELECT metric, value FROM metrics")
    assert len(rows) == 1
    assert rows[0][0] == "compact"
    assert rows[0][1] == 1
    mc.close()


@pytest.mark.asyncio
async def test_record_unknown_event_ignored(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    event = {"event": "user.message", "session": "main/default", "data": {"text": "hi"}}
    await mc.record(event)
    rows = mc._query_raw("SELECT COUNT(*) FROM metrics")
    assert rows[0][0] == 0
    mc.close()


@pytest.mark.asyncio
async def test_query_buckets(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    # Use a fixed base aligned to a bucket boundary to avoid timing flakiness
    base = int(time.time() / 3600) * 3600  # start of current hour
    # Two turns in the previous hour bucket, one in the current
    mc._conn.executemany(
        "INSERT INTO metrics (ts, metric, value, session, agent) VALUES (?, ?, ?, ?, ?)",
        [
            (base - 1800, "turn", 1, "main/default", "main"),  # prev hour
            (base - 900, "turn", 1, "main/default", "main"),   # prev hour
            (base + 100, "turn", 1, "main/default", "main"),   # current hour
        ],
    )
    mc._conn.commit()

    result = mc.query("turn", start_ts=base - 3600, end_ts=base + 3600, bucket_seconds=3600)
    assert len(result) == 2
    assert result[0]["value"] == 2  # two turns in first bucket
    assert result[1]["value"] == 1  # one turn in second bucket
    mc.close()


@pytest.mark.asyncio
async def test_summary_today(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    # Seed some data for "today"
    now = time.time()
    mc._conn.executemany(
        "INSERT INTO metrics (ts, metric, value, session, agent) VALUES (?, ?, ?, ?, ?)",
        [
            (now - 60, "turn", 1, "main/default", "main"),
            (now - 30, "turn", 1, "main/default", "main"),
            (now - 60, "tokens_in", 5000, "main/default", "main"),
            (now - 30, "tokens_in", 3000, "main/default", "main"),
            (now - 60, "tokens_out", 1500, "main/default", "main"),
            (now - 30, "tokens_out", 1000, "main/default", "main"),
            (now - 60, "cost", 0.04, "main/default", "main"),
            (now - 30, "cost", 0.02, "main/default", "main"),
        ],
    )
    mc._conn.commit()

    s = mc.summary()
    assert s["turns_today"] == 2
    assert s["tokens_today"]["input"] == 8000
    assert s["tokens_today"]["output"] == 2500
    assert abs(s["cost_today_usd"] - 0.06) < 0.001
    assert s["uptime_seconds"] > 0
    assert s["started_at"] > 0
    mc.close()


def test_prune_with_explicit_days(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    old_ts = time.time() - 8 * 86400  # 8 days ago
    recent_ts = time.time() - 3600     # 1 hour ago
    mc._conn.executemany(
        "INSERT INTO metrics (ts, metric, value, session, agent) VALUES (?, ?, ?, ?, ?)",
        [
            (old_ts, "turn", 1, "main/default", "main"),
            (recent_ts, "turn", 1, "main/default", "main"),
        ],
    )
    mc._conn.commit()

    mc.prune(max_age_days=7)
    rows = mc._query_raw("SELECT COUNT(*) FROM metrics")
    assert rows[0][0] == 1
    mc.close()


def test_prune_uses_configured_retention(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db", retention_days=30)
    old_ts = time.time() - 31 * 86400  # 31 days ago
    recent_ts = time.time() - 3600      # 1 hour ago
    mc._conn.executemany(
        "INSERT INTO metrics (ts, metric, value, session, agent) VALUES (?, ?, ?, ?, ?)",
        [
            (old_ts, "turn", 1, "main/default", "main"),
            (recent_ts, "turn", 1, "main/default", "main"),
        ],
    )
    mc._conn.commit()

    mc.prune()  # uses configured 30 days
    rows = mc._query_raw("SELECT COUNT(*) FROM metrics")
    assert rows[0][0] == 1
    mc.close()


def test_default_retention_is_365_days(tmp_path):
    mc = MetricsCollector(tmp_path / "metrics.db")
    assert mc._retention_days == 365
    mc.close()
