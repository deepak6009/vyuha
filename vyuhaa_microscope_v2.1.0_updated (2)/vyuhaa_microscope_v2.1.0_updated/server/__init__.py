"""
server/__init__.py — Optional FastAPI web server.

Only started when ENABLE_WEB_UI=1 environment variable is set.
Provides OF Connect compatibility and a browser-based fallback UI.

The hardware_registry is passed in — FastAPI never creates hardware,
it only reads from what PySide6 already owns.
"""

from __future__ import annotations

import logging
import threading
import os

log = logging.getLogger("server")


def start_web_server_background(hw_registry) -> threading.Thread:
    """
    Start the FastAPI server in a daemon thread.
    Returns the thread (caller can ignore it).
    """
    def _run():
        try:
            import uvicorn
            from fastapi import FastAPI
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def lifespan(app: FastAPI):
                log.info("FastAPI web server started")
                yield
                log.info("FastAPI web server stopped")

            app = FastAPI(title="Vyuhaa Microscope API", lifespan=lifespan)

            # Add OF Connect compatibility endpoints
            from server.legacy_api import add_v2_endpoints

            class _ThingServerShim:
                """Minimal shim so legacy_api can call camera.grab_frame()."""
                def __init__(self, hw):
                    self.things = {"camera": hw.camera}

            add_v2_endpoints(_ThingServerShim(hw_registry))
            # Note: add_v2_endpoints adds routes to app via thing_server.app
            # Since legacy_api expects thing_server.app, we pass app directly
            # Patch: pass app as both
            _ThingServerShim.app = app
            add_v2_endpoints(type("S", (), {"app": app, "things": {"camera": hw_registry.camera}})())

            # Additional REST endpoints for completeness
            @app.get("/api/v2/instrument/position")
            async def get_position():
                return hw_registry.get_position()

            @app.get("/api/v2/instrument")
            async def get_metadata():
                return hw_registry.get_metadata()

            @app.post("/api/v2/instrument/actions/move")
            async def move(body: dict):
                pos = hw_registry.move_relative(
                    x=int(body.get("x", 0)),
                    y=int(body.get("y", 0)),
                    z=int(body.get("z", 0)),
                )
                return {"status": "ok", "position": pos}

            @app.post("/api/v2/instrument/actions/autofocus")
            async def autofocus():
                import asyncio
                result = await asyncio.get_event_loop().run_in_executor(
                    None, hw_registry.run_autofocus)
                return {"status": "ok", "result": result}

            port = int(os.environ.get("WEB_UI_PORT", "5000"))
            log.info(f"Starting web UI on port {port}")
            uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

        except Exception as exc:
            log.exception(f"Web server failed: {exc}")

    t = threading.Thread(target=_run, daemon=True, name="web-server")
    t.start()
    return t
