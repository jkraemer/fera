from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    session TEXT,
    agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_metric_ts ON metrics(metric, ts);
"""

_RECORDED_EVENTS = frozenset({
    "agent.done",
    "agent.compact",
    "adapter.started",
    "adapter.error",
    "session.created",
    "system.startup",
})


class MetricsCollector:
    """Subscribes to the EventBus and persists runtime metrics to SQLite."""

    def __init__(self, db_path: Path, retention_days: int = 365):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._started_at = time.time()
        self._retention_days = retention_days

    async def record(self, event: dict) -> None:
        """EventBus callback — extract metrics from event and write to SQLite."""
        evt = event.get("event", "")
        session = event.get("session", "")
        if not session or session == "$system" or evt not in _RECORDED_EVENTS:
            return

        data = event.get("data", {})
        agent = session.split("/")[0] if "/" in session else None
        ts = time.time()

        rows: list[tuple] = []

        if evt == "agent.done":
            rows.append((ts, "turn", 1, session, agent))
            if (v := data.get("input_tokens")) is not None:
                rows.append((ts, "tokens_in", v, session, agent))
            if (v := data.get("output_tokens")) is not None:
                rows.append((ts, "tokens_out", v, session, agent))
            if (v := data.get("cost_usd")) is not None:
                rows.append((ts, "cost", v, session, agent))
        elif evt == "agent.compact":
            rows.append((ts, "compact", 1, session, agent))
        elif evt == "adapter.started":
            rows.append((ts, "adapter_start", 1, session, agent))
        elif evt == "adapter.error":
            rows.append((ts, "adapter_error", 1, session, agent))
        elif evt == "session.created":
            rows.append((ts, "session_created", 1, session, agent))
        elif evt == "system.startup":
            rows.append((ts, "gateway_start", 1, session, agent))

        if rows:
            try:
                await asyncio.to_thread(self._write_rows, rows)
            except Exception:
                log.exception("MetricsCollector write error")

    def _write_rows(self, rows: list[tuple]) -> None:
        self._conn.executemany(
            "INSERT INTO metrics (ts, metric, value, session, agent) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def query(
        self,
        metric: str,
        start_ts: float,
        end_ts: float,
        bucket_seconds: int = 3600,
    ) -> list[dict]:
        """Return aggregated time-series for a metric, bucketed by time interval."""
        rows = self._conn.execute(
            """
            SELECT CAST(ts / ? AS INTEGER) * ? AS bucket_ts,
                   SUM(value) AS total
            FROM metrics
            WHERE metric = ? AND ts >= ? AND ts < ?
            GROUP BY bucket_ts
            ORDER BY bucket_ts
            """,
            (bucket_seconds, bucket_seconds, metric, start_ts, end_ts),
        ).fetchall()
        return [{"ts": r[0], "value": r[1]} for r in rows]

    def summary(self) -> dict:
        """Return a summary of today's metrics and gateway uptime."""
        from datetime import datetime, timezone

        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).timestamp()

        def _sum(metric: str) -> float:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(value), 0) FROM metrics WHERE metric = ? AND ts >= ?",
                (metric, today_start),
            ).fetchone()
            return row[0]

        return {
            "uptime_seconds": time.time() - self._started_at,
            "started_at": self._started_at,
            "turns_today": int(_sum("turn")),
            "tokens_today": {
                "input": int(_sum("tokens_in")),
                "output": int(_sum("tokens_out")),
            },
            "cost_today_usd": round(_sum("cost"), 4),
        }

    def prune(self, max_age_days: int | None = None) -> int:
        """Delete metrics older than max_age_days. Defaults to configured retention."""
        days = max_age_days if max_age_days is not None else self._retention_days
        cutoff = time.time() - days * 86400
        cursor = self._conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        self._conn.commit()
        return cursor.rowcount

    def _query_raw(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Low-level query helper (also used in tests)."""
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()
