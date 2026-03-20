from __future__ import annotations

import asyncio
import logging

import uvicorn
import websockets
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket

log = logging.getLogger(__name__)


def create_app(
    static_dir: str,
    gateway_port: int,
    gateway_host: str = "127.0.0.1",
) -> Starlette:
    """Create the web UI ASGI app with a websocket proxy to the gateway."""

    gateway_ws_url = f"ws://{gateway_host}:{gateway_port}"

    async def config_endpoint(request):
        host = request.headers.get("host", "localhost")
        return JSONResponse({"gateway_ws": f"ws://{host}/ws"})

    async def ws_proxy(ws: WebSocket):
        await ws.accept()
        try:
            async with websockets.connect(gateway_ws_url) as gw:

                async def browser_to_gateway():
                    try:
                        while True:
                            data = await ws.receive_text()
                            await gw.send(data)
                    except Exception:
                        pass

                async def gateway_to_browser():
                    try:
                        async for msg in gw:
                            await ws.send_text(msg)
                    except Exception:
                        pass

                b2g = asyncio.create_task(browser_to_gateway())
                g2b = asyncio.create_task(gateway_to_browser())
                _, pending = await asyncio.wait(
                    [b2g, g2b], return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
        except Exception:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    app = Starlette(
        routes=[
            Route("/config.json", config_endpoint),
            WebSocketRoute("/ws", ws_proxy),
        ]
    )
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


def run_server():
    """Entry point for the fera-webui systemd unit."""
    from fera.config import load_config

    logging.basicConfig(level=logging.INFO)
    config = load_config()
    webui = config["webui"]
    gw = config["gateway"]

    app = create_app(webui["static_dir"], gw["port"])

    log.info(
        "Serving web UI on %s:%d (proxying gateway on 127.0.0.1:%d)",
        webui["host"], webui["port"], gw["port"],
    )
    uvicorn.run(app, host=webui["host"], port=webui["port"], log_level="info")
