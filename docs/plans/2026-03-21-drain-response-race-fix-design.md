# Fix: _drain_response race condition with AskUserQuestion

**Date:** 2026-03-21
**Issue:** Session `mm-fera-development` went unresponsive after AskUserQuestion timeout

## Problem

`_drain_response` selects its timeout once per loop iteration:

```python
effective_timeout = (
    QUESTION_INACTIVITY_TIMEOUT        # 24h
    if self._has_pending_questions(session_name)
    else inactivity_timeout            # 300s
)
msg = await asyncio.wait_for(msg_iter.__anext__(), timeout=effective_timeout)
```

When the agent calls `AskUserQuestion`, the SDK emits an `AssistantMessage` (tool use block) to the message stream, then sends a `control_request` that spawns the `_can_use_tool` callback concurrently via `start_soon`. The callback registers the question in `_pending_questions`.

Race: `_drain_response` processes the `AssistantMessage`, loops back, checks `_has_pending_questions` — but the `_can_use_tool` task hasn't run yet. It selects 300s. Once committed to `asyncio.wait_for`, it can't be interrupted by external state changes. 300s later → "dead reader" error → session dies.

Additionally, when the turn dies this way, pending questions are not cancelled, leaving the session in limbo.

## Design (Approach A: asyncio.Event wake-up)

### 1. Wake-up signal

Add `_question_events: dict[str, asyncio.Event]` to `AgentRunner.__init__`.

In `_build_can_use_tool`, after registering in `_pending_questions`, set the event:

```python
self._pending_questions[question_id] = fut
self._question_event(session_name).set()
```

`_question_event(name)` lazily creates the Event if absent.

In `_drain_response`, replace the single `asyncio.wait_for` with a helper `_wait_for_message_or_question(msg_iter, session_name, inactivity_timeout)` that:

1. Creates a task for `msg_iter.__anext__()`
2. If no pending question, races against the question event + inactivity timeout
3. If the event fires first → clear it, restart wait with `QUESTION_INACTIVITY_TIMEOUT`
4. If message arrives → return it
5. If timeout → raise `TimeoutError`

### 2. Auto-recovery

In `_run_turn_pooled` and `_run_turn_ephemeral` exception paths, call `cancel_pending_questions(session_name)` before releasing the pool client. This ensures the `_can_use_tool` callback's `await fut` gets `CancelledError` → returns `PermissionResultDeny` → SDK cleanly ends the tool invocation.

### 3. Event cleanup

`cancel_pending_questions(session)` pops the event from `_question_events` after cancelling futures.

### 4. Tests

1. **Race condition** — start `_drain_response` with no pending question, register one concurrently after short delay, verify no timeout
2. **Auto-recovery** — dead reader timeout cancels pending questions, session is free for next turn
3. **Event cleanup** — cancel_pending_questions removes event from dict
