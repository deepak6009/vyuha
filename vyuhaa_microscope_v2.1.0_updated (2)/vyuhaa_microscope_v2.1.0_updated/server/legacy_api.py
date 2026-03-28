"""Provide endpoints that mimic the v2 API for Vyuhaa Connect discoverability."""

from socket import gethostname

from fastapi import Response

# import labthings_fastapi as lt
from typing import Any

from vyuhaa.things.camera import BaseCamera

FAKE_ROUTES = [
    "/api/v2/",
    "/api/v2/streams/snapshot",
    "/api/v2/instrument/settings/name",
]


class JPEGResponse(Response):
    """A FastAPI response with media_type set for a JPEG image."""

    media_type = "image/jpeg"


def add_v2_endpoints(thing_server: Any) -> None:
    """Add the v2 API endpoints for Vyuhaa Connect discoverability."""
    app = thing_server.app

    # The endpoints below fool Vyuhaa Connect into thinking we are a
    # v2 microscope, so we show up correctly.
    # This is necessary until Connect is rebuilt. See #557.
    @app.get("/routes")
    def routes_stub() -> dict[str, dict]:
        """Return a stub list of routes.

        This is used by OF Connect to identify the microscope.
        """
        return {url: {"url": url, "methods": ["GET"]} for url in FAKE_ROUTES}

    @app.get("/api/v2/streams/snapshot")
    @app.head("/api/v2/streams/snapshot")
    async def thumbnail() -> JPEGResponse:
        """Return a low-resolution snapshot, for compatibility with OF connect."""
        camera_thing = thing_server.things["camera"]
        if not isinstance(camera_thing, BaseCamera):
            raise RuntimeError("Camera thing is not a BaseCamera")
        blob = await camera_thing.lores_mjpeg_stream.grab_frame()
        return JPEGResponse(blob)

    @app.get("/api/v2/instrument/settings/name")
    def get_hostname() -> str:
        """Get the hostname of the device, for compatibility with OF connect."""
        return gethostname()
