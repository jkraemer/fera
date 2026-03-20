---
name: log-review
description: >
  Review Fera system logs for health, errors, and usage statistics. Use for periodic system health checks,
  morning briefing log sections, or when investigating errors. Covers structured app logs
  (/home/fera/logs/) and systemd journal for fera-gateway, fera-knowledge-indexer, and fera-webui.
  Use this skill when asked to check system health, review logs, or investigate errors.
---

# Log Review

## Scripts

**System health & tool stats:**
```bash
python3 .claude/skills/log-review/scripts/log_stats.py [--days N]
# --days 1 (default), --days 7, --json
```

**API cost by day (from transcripts):**
```bash
python3 .claude/skills/log-review/scripts/cost_by_day.py [--days N | --all]
# --days 7 (default), --all for full history
```

## What It Reports

**App logs** (`/home/fera/logs/YYYY/MM/YYYY-MM-DD.jsonl`):
- Turn count, token usage (input/output/cache), gateway restart count
- Tool call counts by tool name, tool errors
- Active sessions ranked by activity
- Error/warning level events

**Systemd journals** (via `journalctl`):
- `fera-gateway` — adapter errors, session issues
- `fera-knowledge-indexer` — indexing failures, OCR errors
- `fera-webui` — HTTP errors

## Known Noise (Suppressed by script)

These are expected and suppressed automatically:
- `No text extracted` — images/PDFs with no text content
- `Ignoring wrong pointing object` — malformed PDF metadata (pypdf)
- `Multiple definitions in dictionary` — duplicate PDF keys (pypdf)
- `mattermostdriver.websocket: Sorry, we could not find the page` — MM websocket reconnect loop

## What to Flag

Report to the user if:
- **Telegram adapter errors** (`adapter.error`) — `Timed out` or `Updater not running`
- **knowledge-indexer errors** beyond pypdf/image noise — e.g. `pdftoppm failed`, `tesseract returned -15`, `BlockingIOError`
- **Unexpected gateway restarts** (>2 in 24h during normal operation)
- **Tool errors** — any tool with repeated failures in `tool_errors`
- **New error patterns** not yet in the suppress list

## Morning Brief Format

Keep it concise. Only include if something is noteworthy — skip entirely if all clean.

```
## 🖥️ System Health

- **Turns:** 42 | **Gateway restarts:** 1
- **Top tools:** Read (120), Bash (98), Grep (34)
- **fera-gateway:** ⚠️ MM websocket errors (noise, suppressed)
- **fera-knowledge-indexer:** ⚠️ pdftoppm failed on 3 PDFs
- **fera-webui:** ✅ clean
- **App errors:** Telegram adapter timed out 2x at 12:04 UTC
```
