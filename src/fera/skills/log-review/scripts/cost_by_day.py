#!/usr/bin/env python3
"""
cost_by_day.py — Compute API cost from Fera transcript files.

Scans all transcript files under ~/data/transcripts/, reads 'done' events,
and aggregates cost_usd and token usage by calendar day (Manila time, UTC+8)
or by session.

Usage:
  python3 cost_by_day.py [--days N]          # by day, default 7
  python3 cost_by_day.py --all               # by day, all history
  python3 cost_by_day.py --by-session        # by session, all history
  python3 cost_by_day.py --by-session --days 3  # by session, last 3 days
"""

import json
import re
import argparse
import collections
from datetime import datetime, timezone, timedelta
from pathlib import Path

TRANSCRIPT_DIR = Path.home() / "data" / "transcripts"
MANILA_OFFSET = timedelta(hours=8)


def manila_date(ts_str: str) -> str:
    """Convert ISO8601 UTC timestamp to Manila date string YYYY-MM-DD."""
    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    manila = ts + MANILA_OFFSET
    return manila.strftime("%Y-%m-%d")


def session_key(path: Path) -> str:
    """Derive agent/session-name from transcript path, stripping timestamp suffix."""
    rel = path.relative_to(TRANSCRIPT_DIR)
    agent = rel.parts[0]
    fname = rel.parts[-1].replace(".jsonl", "")
    base = re.sub(r"-\d{8}T\d{6}$", "", fname)
    return f"{agent}/{base}"


def load_transcripts(days: int | None) -> list[dict]:
    """Load all 'done' events from transcript files, optionally filtered by recency."""
    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    events = []
    for path in sorted(TRANSCRIPT_DIR.glob("**/*.jsonl")):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get("type") != "done":
                            continue
                        ts_str = e.get("ts", "")
                        if not ts_str:
                            continue
                        if cutoff is not None:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            if ts < cutoff:
                                continue
                        e["_session"] = session_key(path)
                        events.append(e)
                    except (json.JSONDecodeError, ValueError):
                        pass
        except (IOError, PermissionError):
            pass
    return events


def empty_bucket() -> dict:
    return {
        "cost_usd": 0.0,
        "turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def accumulate(bucket: dict, e: dict) -> None:
    bucket["cost_usd"] += e.get("cost_usd") or 0.0
    bucket["turns"] += e.get("num_turns") or 1
    bucket["input_tokens"] += e.get("input_tokens") or 0
    bucket["output_tokens"] += e.get("output_tokens") or 0
    bucket["cache_read_tokens"] += e.get("cache_read_input_tokens") or 0
    bucket["cache_creation_tokens"] += e.get("cache_creation_input_tokens") or 0


def analyze_by_day(events: list[dict]) -> dict:
    by_day = collections.defaultdict(empty_bucket)
    for e in events:
        accumulate(by_day[manila_date(e["ts"])], e)
    total = sum(v["cost_usd"] for v in by_day.values())
    return {"rows": dict(sorted(by_day.items())), "total_cost": total}


def analyze_by_session(events: list[dict]) -> dict:
    by_session = collections.defaultdict(empty_bucket)
    for e in events:
        accumulate(by_session[e["_session"]], e)
    total = sum(v["cost_usd"] for v in by_session.values())
    rows = dict(sorted(by_session.items(), key=lambda x: -x[1]["cost_usd"]))
    return {"rows": rows, "total_cost": total}


HEADER = f"{'Session/Date':<40} {'Cost':>10} {'Turns':>6} {'Input':>9} {'Output':>9} {'CacheR':>10} {'CacheW':>9}"
SEP = "-" * 97


def print_row(label: str, d: dict) -> None:
    print(
        f"{label:<40} "
        f"${d['cost_usd']:>9.4f} "
        f"{d['turns']:>6} "
        f"{d['input_tokens']:>9,} "
        f"{d['output_tokens']:>9,} "
        f"{d['cache_read_tokens']:>10,} "
        f"{d['cache_creation_tokens']:>9,}"
    )


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    group.add_argument("--all", action="store_true", help="All available data")
    parser.add_argument("--by-session", action="store_true", help="Group by session instead of day")
    args = parser.parse_args()

    days = None if args.all else args.days
    events = load_transcripts(days)

    label = "all time" if args.all else f"last {days}d"

    if args.by_session:
        result = analyze_by_session(events)
        print(f"\n=== Fera API Cost by Session ({label}) ===\n")
        print(HEADER)
        print(SEP)
        for key, d in result["rows"].items():
            print_row(key, d)
    else:
        result = analyze_by_day(events)
        print(f"\n=== Fera API Cost by Day ({label}, Manila time) ===\n")
        print(HEADER)
        print(SEP)
        for day, d in result["rows"].items():
            print_row(day, d)

    print(SEP)
    total_bucket = empty_bucket()
    total_bucket["cost_usd"] = result["total_cost"]
    # Sum other fields for total row
    for d in result["rows"].values():
        for k in ("turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            total_bucket[k] += d[k]
    print_row("TOTAL", total_bucket)
    print()


if __name__ == "__main__":
    main()
