"""
Demo Dashboard WebSocket + HTTP server.

Serves the reCamera Demo Dashboard (recamera_demo.html) and provides
a WebSocket endpoint for real-time state push + gimbal control.

Runs in a daemon thread — never blocks the control loop.

Usage:
    from dashboard.demo_server import start_demo_server, stop_demo_server
    start_demo_server(host="0.0.0.0", port=8001)
    ...  # control loop runs
    stop_demo_server()

Requires:  pip install aiohttp
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

try:
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    web = None  # type: ignore
    logger.warning("aiohttp not installed — demo dashboard unavailable")


# ═══════════════════════════════════════════════════════════════
#  Server globals
# ═══════════════════════════════════════════════════════════════

PUSH_INTERVAL = 0.08
_DASHBOARD_DIR = Path(__file__).resolve().parent
_INDEX_PATH = _DASHBOARD_DIR / "recamera_demo.html"
_server_thread: Optional[threading.Thread] = None
_runner = None


# ═══════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════

def start_demo_server(host: str = "0.0.0.0", port: int = 8001) -> bool:
    """Start demo dashboard in a daemon thread. Returns True on success."""
    global _server_thread, _runner

    if not HAS_AIOHTTP:
        logger.error("aiohttp required: pip install aiohttp")
        return False

    if _server_thread is not None and _server_thread.is_alive():
        logger.warning("Demo dashboard already running on port %d", port)
        return True

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = _create_app()
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        global _runner
        _runner = runner
        site = web.TCPSite(runner, host, port)
        loop.run_until_complete(site.start())
        display_host = "localhost" if host == "0.0.0.0" else host
        logger.info("🎛️  Demo Dashboard: http://%s:%d  |  ws://%s:%d/ws",
                     display_host, port, display_host, port)
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

    _server_thread = threading.Thread(target=_run, daemon=True, name="demo-dash-srv")
    _server_thread.start()
    time.sleep(0.3)
    return _server_thread.is_alive()


def stop_demo_server() -> None:
    """Stop the demo dashboard server."""
    global _runner, _server_thread
    if _runner is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_runner.cleanup())
        except Exception:
            pass
        finally:
            loop.close()
        _runner = None
    _server_thread = None
    logger.info("Demo dashboard stopped")


# ═══════════════════════════════════════════════════════════════
#  App factory (only if aiohttp available)
# ═══════════════════════════════════════════════════════════════

if HAS_AIOHTTP:

    async def _handle_index(request: web.Request) -> web.Response:
        html = _INDEX_PATH.read_text() if _INDEX_PATH.is_file() else "<h1>reCamera Demo</h1>"
        return web.Response(text=html, content_type="text/html")

    async def _handle_state(request: web.Request) -> web.Response:
        from dashboard.demo_shared_state import demo_dashboard_state
        return web.json_response(demo_dashboard_state.snapshot())

    async def _handle_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "ts": time.time()})

    async def _handle_ws(request: web.Request) -> web.WebSocketResponse:
        from dashboard.demo_shared_state import demo_dashboard_state

        ws = web.WebSocketResponse(heartbeat=5.0)
        await ws.prepare(request)
        request.app["websockets"].add(ws)
        logger.info("Demo WS connected (%d total)", len(request.app["websockets"]))

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    if msg.data == "ping":
                        await ws.send_str("pong")
                    else:
                        try:
                            cmd = json.loads(msg.data)
                            cmd_type = cmd.get("type", "")

                            # ── Gimbal mode command ──
                            if cmd_type == "gimbal_mode":
                                req_mode = cmd.get("mode", "")
                                await ws.send_str(json.dumps({
                                    "type": "mode_ack",
                                    "mode": req_mode,
                                    "ok": False,
                                    "reason": "control_plane_is_core_orchestrator",
                                }))

                            # ── Axis control (manual pan/tilt) ──
                            elif cmd_type == "gimbal_control":
                                await ws.send_str(json.dumps({
                                    "type": "control_ack",
                                    "ok": False,
                                    "reason": "control_plane_is_core_orchestrator",
                                }))

                            # ── Legacy manual_mode ──
                            elif cmd_type == "manual_mode":
                                await ws.send_str(json.dumps({
                                    "type": "mode_ack",
                                    "mode": "manual" if bool(cmd.get("enable", False)) else "ai_track",
                                    "ok": False,
                                    "reason": "control_plane_is_core_orchestrator",
                                }))

                            # ── Legacy manual_control ──
                            elif cmd_type == "manual_control":
                                pan = float(cmd.get("pan", 0))
                                tilt = float(cmd.get("tilt", 0))
                                gimbal_state.set_manual_control(pan, tilt)

                        except (json.JSONDecodeError, ValueError) as e:
                            await ws.send_str(json.dumps({
                                "type": "error",
                                "message": str(e),
                            }))

                elif msg.type == web.WSMsgType.ERROR:
                    logger.warning("Demo WS error: %s", ws.exception())
        finally:
            request.app["websockets"].discard(ws)
        return ws

    async def _push_loop(app: web.Application) -> None:
        from dashboard.demo_shared_state import demo_dashboard_state
        while True:
            try:
                payload = json.dumps(demo_dashboard_state.snapshot())
                for ws in set(app.get("websockets", set())):
                    try:
                        if not ws.closed:
                            await ws.send_str(payload)
                    except Exception:
                        app["websockets"].discard(ws)
                await asyncio.sleep(PUSH_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1.0)

    async def _on_startup(app: web.Application) -> None:
        app["websockets"] = set()
        app["push_task"] = asyncio.create_task(_push_loop(app))

    async def _on_cleanup(app: web.Application) -> None:
        task = app.get("push_task")
        if task:
            task.cancel()
        for ws in set(app.get("websockets", set())):
            await ws.close()

    def _create_app() -> web.Application:
        app = web.Application()
        app.router.add_get("/", _handle_index)
        app.router.add_get("/state", _handle_state)
        app.router.add_get("/health", _handle_health)
        app.router.add_get("/ws", _handle_ws)
        app.on_startup.append(_on_startup)
        app.on_cleanup.append(_on_cleanup)
        return app


# ═══════════════════════════════════════════════════════════════
#  Standalone test
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import signal
    from utils.logger import setup_root_logger
    setup_root_logger("INFO")
    from dashboard.demo_shared_state import demo_dashboard_state
    demo_dashboard_state.update(
        state="TRACK", bbox=[450, 320, 560, 500],
        center=[505, 410], error=[-455, -130],
        norm=[-0.24, -0.12], filtered=[-0.59, -0.30],
        send=[-0.59, -0.30], fps=14.8, frame_id=42,
        doa_azimuth=23.5, doa_age=0.15, doa_source="mock",
        doa_connected=True,
    )
    if not start_demo_server():
        print("aiohttp required: pip install aiohttp")
        raise SystemExit(1)
    print("Demo Dashboard: http://localhost:8001 — Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_demo_server()
