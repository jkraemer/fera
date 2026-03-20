"""Cron job execution — session and one-shot modes."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import TYPE_CHECKING

from fera.logger import get_logger

if TYPE_CHECKING:
    from fera.adapters.bus import EventBus
    from fera.gateway.runner import AgentRunner
    from fera.gateway.sessions import SessionManager

log = logging.getLogger(__name__)


async def execute_job(
    *,
    job_name: str,
    job: dict,
    runner: AgentRunner,
    bus: EventBus,
    sessions: SessionManager,
) -> dict:
    """Execute a cron job. Returns a result dict with status."""
    session = job.get("session")
    payload = job["payload"]
    prompt_mode = job.get("prompt_mode", "full" if session else "minimal")
    model = job.get("model")
    allowed_tools = job.get("allowed_tools")

    agent = job.get("agent", "main")

    # Qualify bare session names with the agent so the session (and its
    # workspace directory) are created under the correct agent.
    if session and "/" not in session:
        session = f"{agent}/{session}"

    if logger := get_logger():
        await logger.log("cron.started", job=job_name, agent=agent)

    if session:
        result = await _execute_session_job(
            job_name=job_name,
            session=session,
            payload=payload,
            prompt_mode=prompt_mode,
            model=model,
            allowed_tools=allowed_tools,
            runner=runner,
            bus=bus,
            sessions=sessions,
        )
    else:
        result = await _execute_ephemeral_job(
            job_name=job_name,
            agent=agent,
            payload=payload,
            prompt_mode=prompt_mode,
            model=model,
            allowed_tools=allowed_tools,
            runner=runner,
        )

    if logger := get_logger():
        await logger.log("cron.completed", job=job_name, agent=agent, output=result.get("output", ""))

    return result


async def _execute_session_job(
    *,
    job_name: str,
    session: str,
    payload: str,
    prompt_mode: str,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
    runner: AgentRunner,
    bus: EventBus,
    sessions: SessionManager,
) -> dict:
    """Run a cron job in a named persistent session."""
    sessions.get_or_create(session)
    async for event in runner.run_turn(
        session, payload, source="cron", prompt_mode=prompt_mode,
        model=model, allowed_tools=allowed_tools,
    ):
        await bus.publish(event)
    return {"status": "completed", "job": job_name, "session": session}


async def _execute_ephemeral_job(
    *,
    job_name: str,
    agent: str,
    payload: str,
    prompt_mode: str,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
    runner: AgentRunner,
) -> dict:
    """Run a cron job as a one-shot query."""
    text_parts: list[str] = []
    async for event in runner.run_oneshot(
        payload, agent_name=agent, prompt_mode=prompt_mode, model=model,
        allowed_tools=allowed_tools,
    ):
        if event.get("event") == "agent.text":
            text = event.get("data", {}).get("text", "")
            if text:
                text_parts.append(text)

    return {"status": "completed", "job": job_name, "output": "".join(text_parts)}


async def _run_job_via_gateway(job_name: str) -> None:
    """Connect to the gateway and execute a cron job."""
    import websockets

    from fera.config import ensure_auth_token, load_config, load_cron_config
    from fera.gateway.protocol import make_request

    cron_config = load_cron_config()
    if job_name not in cron_config["jobs"]:
        print(f"Error: job '{job_name}' not found in cron.json", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    gw = config["gateway"]
    host = gw["host"]
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = gw["port"]
    token = ensure_auth_token(config, save=False)

    url = f"ws://{host}:{port}"
    async with websockets.connect(url) as ws:
        # Authenticate
        auth_req = make_request("connect", {"token": token})
        await ws.send(json.dumps(auth_req))
        auth_resp = json.loads(await ws.recv())
        if not auth_resp.get("ok"):
            print(f"Error: authentication failed: {auth_resp.get('error')}", file=sys.stderr)
            sys.exit(1)

        # Send cron.run request
        run_req = make_request("cron.run", {"job": job_name})
        await ws.send(json.dumps(run_req))

        # Wait for the response frame, skipping events
        while True:
            raw = await ws.recv()
            frame = json.loads(raw)
            if frame.get("type") == "res" and frame.get("id") == run_req["id"]:
                if frame.get("ok"):
                    print(f"Job '{job_name}' completed successfully.")
                else:
                    print(f"Error: {frame.get('error')}", file=sys.stderr)
                    sys.exit(1)
                break


def main() -> None:
    """CLI entry point: fera-run-job <name>"""
    if len(sys.argv) != 2:
        print("Usage: fera-run-job <name>", file=sys.stderr)
        sys.exit(1)

    job_name = sys.argv[1]

    # Quick local validation — fail fast without a gateway round-trip
    from fera.config import load_cron_config

    cron_config = load_cron_config()
    if job_name not in cron_config["jobs"]:
        print(f"Error: job '{job_name}' not found in cron.json", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run_job_via_gateway(job_name))
