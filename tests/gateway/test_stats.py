import pytest
from fera.gateway.stats import SessionStats


def test_empty_stats():
    stats = SessionStats()
    s = stats.get("default")
    assert s["turns"] == 0
    assert s["total_input_tokens"] == 0
    assert s["total_output_tokens"] == 0
    assert s["model"] is None
    assert s["context_pct"] is None


def test_record_turn():
    stats = SessionStats()
    stats.record_turn("default", {
        "input_tokens": 3,
        "output_tokens": 200,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": 50000,
        "model": "claude-opus-4-6",
        "duration_ms": 3000,
        "cost_usd": 0.04,
    })
    s = stats.get("default")
    assert s["turns"] == 1
    assert s["total_input_tokens"] == 3
    assert s["total_output_tokens"] == 200
    assert s["model"] == "claude-opus-4-6"
    assert s["last_cost_usd"] == 0.04
    assert s["total_cost_usd"] == 0.04


def test_context_pct_uses_all_input_token_types():
    """Context % is calculated from sum of input_tokens + cache_creation + cache_read."""
    stats = SessionStats()
    stats.record_turn("default", {
        "input_tokens": 100,
        "cache_creation_input_tokens": 5000,
        "cache_read_input_tokens": 95000,
        "output_tokens": 500,
    })
    s = stats.get("default")
    # (100 + 5000 + 95000) / 200000 * 100 = 50.05
    assert s["context_pct"] == 50.0  # round(50.05, 1) == 50.0 (banker's rounding)


def test_context_pct_with_only_input_tokens():
    """Falls back to just input_tokens if cache fields are absent."""
    stats = SessionStats()
    stats.record_turn("default", {
        "input_tokens": 100000,
        "output_tokens": 5000,
    })
    s = stats.get("default")
    assert s["context_pct"] == 50.0


def test_record_multiple_turns_accumulates():
    stats = SessionStats()
    stats.record_turn("default", {
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.01,
    })
    stats.record_turn("default", {
        "input_tokens": 200,
        "output_tokens": 100,
        "cost_usd": 0.02,
    })
    s = stats.get("default")
    assert s["turns"] == 2
    assert s["total_input_tokens"] == 300
    assert s["total_output_tokens"] == 150
    assert s["total_cost_usd"] == 0.03
    assert s["last_cost_usd"] == 0.02


def test_record_compact():
    stats = SessionStats()
    stats.record_turn("default", {"input_tokens": 150000, "output_tokens": 1000})
    stats.record_compact("default", pre_tokens=167000)
    s = stats.get("default")
    assert s["compactions"] == 1


def test_multiple_sessions():
    stats = SessionStats()
    stats.record_turn("work", {"input_tokens": 100, "output_tokens": 50})
    stats.record_turn("personal", {"input_tokens": 200, "output_tokens": 100})
    assert stats.get("work")["total_input_tokens"] == 100
    assert stats.get("personal")["total_input_tokens"] == 200


def test_get_all():
    stats = SessionStats()
    stats.record_turn("a", {"input_tokens": 10, "output_tokens": 5})
    stats.record_turn("b", {"input_tokens": 20, "output_tokens": 10})
    all_stats = stats.get_all()
    assert "a" in all_stats
    assert "b" in all_stats
    assert all_stats["a"]["total_input_tokens"] == 10
    assert all_stats["b"]["total_input_tokens"] == 20


def test_model_preserved_across_turns():
    """Model from earlier turn is preserved if later turn has no model."""
    stats = SessionStats()
    stats.record_turn("default", {"model": "claude-opus-4-6", "input_tokens": 100, "output_tokens": 50})
    stats.record_turn("default", {"input_tokens": 200, "output_tokens": 100})
    s = stats.get("default")
    assert s["model"] == "claude-opus-4-6"


def test_get_returns_copy():
    """Modifying returned dict should not affect internal state."""
    stats = SessionStats()
    stats.record_turn("default", {"input_tokens": 100, "output_tokens": 50})
    s = stats.get("default")
    s["turns"] = 999
    assert stats.get("default")["turns"] == 1
