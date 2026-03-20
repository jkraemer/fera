import time
import pytest
from fera.gateway.metrics import MetricsCollector


@pytest.mark.asyncio
async def test_summary_empty(tmp_path):
    """Summary on empty DB returns sensible defaults."""
    mc = MetricsCollector(tmp_path / "metrics.db")
    s = mc.summary()
    assert s["turns_today"] == 0
    assert s["tokens_today"]["input"] == 0
    assert s["tokens_today"]["output"] == 0
    assert s["cost_today_usd"] == 0
    assert s["uptime_seconds"] >= 0
    mc.close()


@pytest.mark.asyncio
async def test_metrics_query_empty(tmp_path):
    """Query on empty DB returns empty list."""
    mc = MetricsCollector(tmp_path / "metrics.db")
    result = mc.query("turn", time.time() - 86400, time.time(), 3600)
    assert result == []
    mc.close()


@pytest.mark.asyncio
async def test_full_round_trip(tmp_path):
    """Record events via record(), then query them back."""
    mc = MetricsCollector(tmp_path / "metrics.db")

    for i in range(5):
        await mc.record({
            "event": "agent.done",
            "session": "main/default",
            "data": {
                "input_tokens": 1000,
                "output_tokens": 300,
                "cost_usd": 0.01,
            },
        })

    s = mc.summary()
    assert s["turns_today"] == 5
    assert s["cost_today_usd"] == 0.05

    series = mc.query("turn", time.time() - 3600, time.time() + 60, 3600)
    assert len(series) == 1
    assert series[0]["value"] == 5
    mc.close()
