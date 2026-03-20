#!/usr/bin/env python3
"""
log_stats.py — Fera system log analyzer

Outputs a JSON summary of:
- Tool usage counts (from structured app logs)
- Turn stats: count, total cost, token usage
- Error/warning events
- Session activity

Usage:
  python3 log_stats.py [--days N]   # default: 1 (today only)
  python3 log_stats.py --days 7     # last 7 days
"""

import json
import sys
import glob
import argparse
import collections
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOG_DIR = Path("/home/fera/logs")


def load_logs(days: int) -> list[dict]:
    events = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for path in sorted(LOG_DIR.glob("**/*.jsonl")):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        ts_str = e.get("ts", "")
                        if ts_str:
                            # Parse ISO8601 timestamp
                            ts_str_clean = ts_str.replace("+00:00", "+00:00")
                            ts = datetime.fromisoformat(ts_str_clean)
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            if ts >= cutoff:
                                events.append(e)
                    except (json.JSONDecodeError, ValueError):
                        pass
        except (IOError, PermissionError):
            pass
    return events


def analyze(events: list[dict]) -> dict:
    tool_counts = collections.Counter()
    tool_errors = collections.Counter()
    sessions = collections.Counter()
    turns = 0
    total_cost = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read = 0
    total_cache_creation = 0
    errors = []
    warnings = []
    session_resumes = 0
    startups = 0
    shutdowns = 0

    for e in events:
        event = e.get("event", "")
        level = e.get("level", "info")
        data = e.get("data", {}) or {}
        session = e.get("session", "")

        if event == "tool.call":
            name = data.get("tool_name", "unknown")
            tool_counts[name] += 1
            if session:
                sessions[session] += 1

        elif event == "tool.result":
            if data.get("is_error"):
                name = data.get("tool_name", "unknown")
                tool_errors[name] += 1

        elif event == "turn.completed":
            turns += 1
            cost = data.get("cost_usd") or 0
            total_cost += cost
            total_input_tokens += data.get("input_tokens") or 0
            total_output_tokens += data.get("output_tokens") or 0
            total_cache_read += data.get("cache_read_tokens") or 0
            total_cache_creation += data.get("cache_creation_tokens") or 0

        elif event == "session.resumed":
            session_resumes += 1

        elif event == "system.startup":
            startups += 1

        elif event == "system.shutdown":
            shutdowns += 1

        if level == "error":
            errors.append({"ts": e.get("ts"), "event": event, "session": session, "data": data})

        elif level == "warning":
            warnings.append({"ts": e.get("ts"), "event": event, "session": session, "data": data})

    return {
        "tool_counts": dict(tool_counts.most_common()),
        "tool_errors": dict(tool_errors.most_common()),
        "active_sessions": dict(sessions.most_common()),
        "turns": turns,
        "cost_usd": round(total_cost, 4),
        "tokens": {
            "input": total_input_tokens,
            "output": total_output_tokens,
            "cache_read": total_cache_read,
            "cache_creation": total_cache_creation,
        },
        "system": {
            "startups": startups,
            "shutdowns": shutdowns,
            "session_resumes": session_resumes,
        },
        "errors": errors[-20:],    # last 20
        "warnings": warnings[-20:],
    }


def get_journald(unit: str, since_hours: int = 24) -> tuple[int, int, list[str]]:
    """Run journalctl for a unit, return (error_count, warning_count, notable_lines)."""
    import subprocess
    since = (datetime.now() - timedelta(hours=since_hours)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, f"--since={since}", "--no-pager", "-o", "short"],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.splitlines()
    except Exception:
        return 0, 0, []

    error_count = 0
    warning_count = 0
    notable = []

    # Patterns to suppress (known noise — benign/recurring)
    suppress = [
        "No text extracted",           # knowledge-indexer: images/PDFs with no extractable text
        "Ignoring wrong pointing object",  # pypdf: malformed PDF metadata
        "Multiple definitions in dictionary",  # pypdf: duplicate PDF keys
        "mattermostdriver.websocket:Sorry, we could not find the page",  # MM websocket reconnect noise
    ]

    for line in lines:
        low = line.lower()
        suppressed = any(s in line for s in suppress)
        if "error" in low or "critical" in low or "exception" in low:
            error_count += 1
            if not suppressed:
                notable.append(line)
        elif "warn" in low:
            warning_count += 1
            if not suppressed:
                notable.append(line)

    return error_count, warning_count, notable[-10:]  # cap at 10 notable lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    events = load_logs(args.days)
    stats = analyze(events)

    # Journald checks
    units = ["fera-gateway", "fera-knowledge-indexer", "fera-webui"]
    journald = {}
    for unit in units:
        errors, warnings, notable = get_journald(unit, since_hours=args.days * 24)
        journald[unit] = {"errors": errors, "warnings": warnings, "notable": notable}
    stats["journald"] = journald

    if args.json:
        print(json.dumps(stats, indent=2))
        return

    # Human-readable output
    print(f"\n=== Fera Log Review (last {args.days}d) ===\n")

    print(f"Turns: {stats['turns']}  |  Cost: ${stats['cost_usd']:.4f} USD")
    t = stats["tokens"]
    print(f"Tokens — input: {t['input']:,}  output: {t['output']:,}  "
          f"cache_read: {t['cache_read']:,}  cache_creation: {t['cache_creation']:,}")
    s = stats["system"]
    print(f"Gateway restarts: {s['startups']}  |  Session resumes: {s['session_resumes']}\n")

    print("Top tool calls:")
    for name, count in list(stats["tool_counts"].items())[:15]:
        err = stats["tool_errors"].get(name, 0)
        err_str = f"  ({err} errors)" if err else ""
        print(f"  {count:4d}  {name}{err_str}")

    if stats["tool_errors"]:
        print("\nTool errors:")
        for name, count in stats["tool_errors"].items():
            print(f"  {count:3d}x  {name}")

    print("\nActive sessions (by tool calls):")
    for session, count in list(stats["active_sessions"].items())[:8]:
        print(f"  {count:4d}  {session}")

    print("\n--- Systemd Units ---")
    for unit, data in stats["journald"].items():
        status = "✅" if data["errors"] == 0 else "⚠️"
        print(f"{status} {unit}: {data['errors']} errors, {data['warnings']} warnings")
        for line in data["notable"]:
            print(f"     {line[-120:]}")

    if stats["errors"]:
        print(f"\nApp log errors ({len(stats['errors'])} recent):")
        for e in stats["errors"][-5:]:
            print(f"  [{e['ts']}] {e['event']} / {e['session']} — {e['data']}")

    print()


if __name__ == "__main__":
    main()
