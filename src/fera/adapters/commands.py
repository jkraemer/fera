from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fera.adapters.base import AdapterContext

log = logging.getLogger(__name__)

_COMMANDS = ("/model", "/session", "/sessions", "/status", "/stop", "/clear")


@dataclass
class CommandResult:
    response: str


class SlashCommandHandler:
    def __init__(self, context: AdapterContext) -> None:
        self._context = context

    def match(self, text: str) -> bool:
        stripped = text.strip()
        for cmd in _COMMANDS:
            if stripped == cmd or stripped.startswith(cmd + " "):
                return True
        if self._model_shortcut(stripped):
            return True
        return False

    def _model_shortcut(self, text: str) -> str | None:
        """If text is '/<alias>' for a known model alias, return the alias."""
        if not text.startswith("/") or " " in text:
            return None
        alias = text[1:]
        models = self._context.load_models()
        if alias in models:
            return alias
        return None

    async def handle(self, text: str, current_session: str) -> CommandResult | None:
        stripped = text.strip()
        log.info("command %r session=%s", stripped, current_session)
        if stripped == "/model":
            result = self._list_models(current_session)
        elif stripped.startswith("/model "):
            result = await self._switch_model(stripped[7:].strip(), current_session)
        elif stripped == "/session":
            result = self._status(current_session)
        elif stripped.startswith("/session "):
            result = CommandResult(response="Session switching is not supported.")
        elif stripped == "/sessions":
            result = self._list_sessions()
        elif stripped == "/status":
            result = self._status(current_session)
        elif stripped == "/stop":
            result = await self._stop(current_session)
        elif stripped == "/clear":
            result = await self._clear(current_session)
        elif alias := self._model_shortcut(stripped):
            result = await self._switch_model(alias, current_session)
        else:
            return None
        log.info("command %r result: %r", stripped, result.response)
        return result

    def _list_sessions(self) -> CommandResult:
        sessions = self._context.list_sessions()
        lines = []
        for s in sessions:
            name = s.get("name", s.get("id", "?"))
            lines.append(f"  {name}")
        msg = "Sessions:\n" + "\n".join(lines) if lines else "No sessions"
        return CommandResult(response=msg)

    def _status(self, current_session: str) -> CommandResult:
        stats = self._context.session_stats(current_session)
        ctx_pct = stats.get("context_pct")
        model = stats.get("model") or "unknown"
        turns = stats.get("turns", 0)
        total_in = stats.get("total_input_tokens", 0)
        total_out = stats.get("total_output_tokens", 0)
        compactions = stats.get("compactions", 0)
        total_cost = stats.get("total_cost_usd", 0)

        lines = [f"Session: {current_session}"]
        if ctx_pct is not None:
            remaining = round(100 - ctx_pct, 1)
            lines.append(f"Context: {remaining}% remaining")
        else:
            lines.append("Context: no data yet")
        lines.append(f"Model: {model}")
        lines.append(f"Turns: {turns}")
        lines.append(f"Tokens: {total_in:,} in / {total_out:,} out")
        if compactions:
            lines.append(f"Compactions: {compactions}")
        if total_cost:
            lines.append(f"Cost: ${total_cost:.4f}")
        return CommandResult(response="\n".join(lines))

    def _list_models(self, current_session: str) -> CommandResult:
        qualified = self._context._qualify_session(current_session)
        override = self._context._runner._session_models.get(qualified)
        stats = self._context.session_stats(current_session)
        current = override or stats.get("model") or "unknown"
        models = self._context.load_models()

        lines = [f"Current model: {current}"]
        if models:
            lines.append("Available models:")
            for alias, model_id in sorted(models.items()):
                marker = " (active)" if model_id == current else ""
                lines.append(f"  {alias} → {model_id}{marker}")
        else:
            lines.append("No models.json found — using SDK default")
        return CommandResult(response="\n".join(lines))

    async def _stop(self, session: str) -> CommandResult:
        interrupted = await self._context.interrupt_session(session)
        if interrupted:
            return CommandResult(response="Interrupted.")
        return CommandResult(response="No active turn to interrupt.")

    async def _clear(self, session: str) -> CommandResult:
        try:
            status = await self._context.clear_session(session)
            if status:
                msg = f"Done ({status}). Fresh context on next message."
            else:
                msg = "Done. Fresh context on next message."
            return CommandResult(response=msg)
        except Exception:
            log.exception("clear_session failed for session %s", session)
            return CommandResult(response="Something went wrong — check gateway logs.")

    async def _switch_model(self, alias: str, current_session: str) -> CommandResult:
        from fera.config import resolve_model

        if not self._context._runner._pool:
            return CommandResult(
                response="Model switching requires a pooled session."
            )
        try:
            fera_home = self._context._fera_home
            resolved = resolve_model(alias, fera_home)
            if resolved is None:
                return CommandResult(response="No model specified.")
            await self._context.set_model(current_session, alias)
            return CommandResult(response=f"Switched to {resolved}")
        except ValueError as e:
            return CommandResult(response=str(e))
