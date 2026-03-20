import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from fera.logger import EventLogger, get_logger, init_logger


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / "logs"


@pytest.fixture(autouse=True)
def cleanup_logger():
    yield
    import fera.logger as logger_mod
    if logger_mod._logger is not None:
        logger_mod._logger.close()
        logger_mod._logger = None


@pytest.mark.asyncio
async def test_log_creates_daily_file(log_dir):
    logger = EventLogger(log_dir)
    await logger.log("system.startup", version="0.1.0")
    today = date.today()
    expected = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    assert expected.exists()


@pytest.mark.asyncio
async def test_log_entry_format(log_dir):
    logger = EventLogger(log_dir)
    await logger.log("system.startup", version="0.1.0")
    today = date.today()
    path = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    entry = json.loads(path.read_text().strip())
    assert entry["event"] == "system.startup"
    assert entry["level"] == "info"
    assert entry["session"] is None
    assert entry["data"]["version"] == "0.1.0"
    assert "ts" in entry


@pytest.mark.asyncio
async def test_log_with_session(log_dir):
    logger = EventLogger(log_dir)
    await logger.log("turn.started", session="main", source="web")
    today = date.today()
    path = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    entry = json.loads(path.read_text().strip())
    assert entry["session"] == "main"
    assert entry["data"]["source"] == "web"


@pytest.mark.asyncio
async def test_log_multiple_entries_newline_delimited(log_dir):
    logger = EventLogger(log_dir)
    await logger.log("system.startup")
    await logger.log("system.shutdown", reason="test")
    today = date.today()
    path = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "system.startup"
    assert json.loads(lines[1])["event"] == "system.shutdown"


@pytest.mark.asyncio
async def test_log_error_level(log_dir):
    logger = EventLogger(log_dir)
    await logger.log("adapter.error", level="error", adapter="telegram", error="fail")
    today = date.today()
    path = log_dir / str(today.year) / f"{today.month:02d}" / f"{today}.jsonl"
    entry = json.loads(path.read_text().strip())
    assert entry["level"] == "error"


@pytest.mark.asyncio
async def test_broadcast_called_on_log(log_dir):
    logger = EventLogger(log_dir)
    received = []

    async def cb(entry):
        received.append(entry)

    logger.set_broadcast(cb)
    await logger.log("system.startup", version="0.1.0")
    assert len(received) == 1
    assert received[0]["event"] == "system.startup"


@pytest.mark.asyncio
async def test_broadcast_not_required(log_dir):
    """Logger works fine with no broadcast callback set."""
    logger = EventLogger(log_dir)
    await logger.log("system.startup")  # should not raise


def test_get_logger_returns_none_if_not_initialized(tmp_path):
    import fera.logger as logger_mod
    logger_mod._logger = None  # reset singleton
    assert get_logger() is None


def test_init_logger_sets_singleton(tmp_path):
    import fera.logger as logger_mod
    logger_mod._logger = None
    logger = init_logger(tmp_path / "logs")
    assert get_logger() is logger
    logger_mod._logger = None  # clean up
