"""
hardware_registry.py — Shared hardware ownership for Vyuhaa Jetson.

The PySide6 operator app populates this singleton at startup.
The RemoteSharingService reads from it when a remote viewer connects.

Rules:
  - PySide6 app creates and owns all hardware objects.
  - Nothing else instantiates or reconfigures hardware.
  - RemoteSharingService calls read-only methods via this registry.
  - All methods are thread-safe (RemoteSharingService runs in a background thread).
"""

from __future__ import annotations

import base64
import io
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger("hardware_registry")


@dataclass
class HardwareRegistry:
    """
    Central reference store for hardware objects owned by the PySide6 app.

    Instantiate once at module level (see bottom of file).
    PySide6 app assigns .camera, .stage, .autofocus after hardware init.
    RemoteSharingService calls the helper methods below.
    """

    # Set by PySide6 app after initialisation
    camera:    Optional[object] = None   # things.camera.BaseCamera subclass
    stage:     Optional[object] = None   # things.stage.BaseStage subclass
    autofocus: Optional[object] = None   # things.autofocus.AutofocusThing

    # Scan state — written by ScanManager, read by dispatch
    scan_manager: Optional[object] = None

    # Internal lock — ensures remote thread never races the operator thread
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ── Readiness ────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        """Return True if the minimum hardware (camera + stage) is available."""
        return self.camera is not None and self.stage is not None

    # ── Camera helpers ───────────────────────────────────────────────────────

    def grab_jpeg_bytes(self) -> bytes:
        """
        Return the latest frame as raw JPEG bytes.
        Reads from the camera's mjpeg_stream — does NOT pause the live preview.
        Thread-safe.
        """
        with self._lock:
            if self.camera is None:
                raise RuntimeError("Camera not initialised")

            # Fast path: JetsonCamera keeps _last_display_frame (BGR)
            last = getattr(self.camera, "_last_display_frame", None)
            if last is not None:
                import cv2
                ok, buf = cv2.imencode(".jpg", last, [cv2.IMWRITE_JPEG_QUALITY, 55])
                if ok:
                    return buf.tobytes()

            # Universal fallback: capture_array() returns RGB ndarray
            # Works for SimulatedCamera, OpenCVCamera, JetsonCamera
            import cv2, numpy as np
            arr = self.camera.capture_array()
            if arr is None:
                raise RuntimeError("No frame available yet")
            arr = np.ascontiguousarray(arr, dtype=np.uint8)
            if arr.ndim == 3 and arr.shape[2] == 3:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 55])
            if not ok:
                raise RuntimeError("JPEG encode failed")
            return buf.tobytes()

    def grab_jpeg_b64(self) -> str:
        """Return latest frame as a base64-encoded JPEG string."""
        return base64.b64encode(self.grab_jpeg_bytes()).decode()

    # ── Stage helpers ────────────────────────────────────────────────────────

    def get_position(self) -> dict:
        """Return current stage position as {x, y, z}. Thread-safe."""
        with self._lock:
            if self.stage is None:
                raise RuntimeError("Stage not initialised")
            return dict(self.stage.position)

    def move_relative(self, x: int = 0, y: int = 0, z: int = 0) -> dict:
        """
        Relative stage move.
        Runs in the caller's thread — blocks until the move completes.
        Thread-safe via the internal lock.
        """
        with self._lock:
            if self.stage is None:
                raise RuntimeError("Stage not initialised")
            self.stage.move_relative(x=x, y=y, z=z)
            return dict(self.stage.position)

    def move_absolute(self, x: int, y: int, z: int) -> dict:
        """Absolute stage move. Thread-safe."""
        with self._lock:
            if self.stage is None:
                raise RuntimeError("Stage not initialised")
            self.stage.move_absolute(x=x, y=y, z=z)
            return dict(self.stage.position)

    def set_zero(self) -> dict:
        """Zero stage coordinates at current position. Thread-safe."""
        with self._lock:
            if self.stage is None:
                raise RuntimeError("Stage not initialised")
            self.stage.set_zero_position()
            return dict(self.stage.position)

    # ── Autofocus helpers ────────────────────────────────────────────────────

    def run_autofocus(self, dz: int = 10000) -> dict:
        """
        Run Laplacian autofocus sweep.
        Blocks until complete — called from executor thread by RemoteSharingService.
        """
        with self._lock:
            if self.autofocus is None:
                raise RuntimeError("Autofocus not initialised")
            return self.autofocus.laplacian_autofocus(dz=dz)

    # ── Metadata ─────────────────────────────────────────────────────────────

    def get_metadata(self) -> dict:
        """Return basic microscope metadata."""
        import socket
        meta = {
            "hostname": socket.gethostname(),
            "make":     "Vyuhaa",
            "model":    "Vyuhaa LBC Microscope",
        }
        if self.stage:
            meta["position"] = self.get_position()
        if self.camera:
            meta["camera_streaming"] = self.camera.stream_active
        return meta


# ── Module-level singleton ────────────────────────────────────────────────────
# Import this everywhere:  from hardware_registry import hardware
hardware = HardwareRegistry()
