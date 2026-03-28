"""
scan_manager.py — Thread-safe scan state management.

SmartScan runs in a background thread started by the PySide6 app.
RemoteSharingService reads progress via ScanManager.
The operator UI controls the scan via ScanManager methods.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Callable, Optional

log = logging.getLogger("scan_manager")


class ScanState(Enum):
    IDLE      = auto()
    RUNNING   = auto()
    PAUSED    = auto()
    CANCELLED = auto()
    COMPLETE  = auto()
    ERROR     = auto()


@dataclass
class ScanProgress:
    state:       ScanState = ScanState.IDLE
    tile_index:  int = 0
    total_tiles: int = 0
    label:       str = ""
    error:       str = ""
    started_at:  Optional[float] = None
    ended_at:    Optional[float] = None

    @property
    def percent(self) -> float:
        if self.total_tiles == 0:
            return 0.0
        return round(100.0 * self.tile_index / self.total_tiles, 1)

    def to_dict(self) -> dict:
        return {
            "state":       self.state.name,
            "tile_index":  self.tile_index,
            "total_tiles": self.total_tiles,
            "percent":     self.percent,
            "label":       self.label,
            "error":       self.error,
        }


class ScanManager:
    """
    Owns the scan lifecycle: start, pause, resume, cancel.
    All public methods are thread-safe.
    """

    def __init__(self, hardware_registry) -> None:
        self._hw         = hardware_registry
        self._lock       = threading.Lock()
        self._progress   = ScanProgress()
        self._thread:    Optional[threading.Thread] = None
        self._pause_ev   = threading.Event()
        self._cancel_ev  = threading.Event()
        self._pause_ev.set()   # not paused initially

        # Callbacks for PySide6 signals — set by main.py
        self.on_progress: Optional[Callable[[ScanProgress], None]] = None
        self.on_complete: Optional[Callable[[ScanProgress], None]] = None
        self.on_error:    Optional[Callable[[str], None]] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, cols: int, rows: int, overlap: int,
              pattern: str, label: str, objective: str,
              images_dir: Optional[str] = None) -> bool:
        """Start a new scan. Returns False if a scan is already running."""
        with self._lock:
            if self._progress.state == ScanState.RUNNING:
                log.warning("Scan already running — ignoring start request")
                return False
            if images_dir is None:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                images_dir = os.path.expanduser(f"~/vyuhaa_scans/{label or ts}")
            os.makedirs(images_dir, exist_ok=True)

            self._progress = ScanProgress(
                state       = ScanState.RUNNING,
                total_tiles = cols * rows,
                label       = label,
                started_at  = time.time(),
            )
            self._pause_ev.set()
            self._cancel_ev.clear()

        self._thread = threading.Thread(
            target=self._run_scan,
            args=(cols, rows, overlap, pattern, objective, images_dir),
            daemon=True,
        )
        self._thread.start()
        log.info(f"Scan started: {cols}x{rows} tiles → {images_dir}")
        return True

    def pause(self) -> None:
        """Pause a running scan after the current tile completes."""
        with self._lock:
            if self._progress.state == ScanState.RUNNING:
                self._progress.state = ScanState.PAUSED
                self._pause_ev.clear()
                log.info("Scan paused")

    def resume(self) -> None:
        """Resume a paused scan."""
        with self._lock:
            if self._progress.state == ScanState.PAUSED:
                self._progress.state = ScanState.RUNNING
                self._pause_ev.set()
                log.info("Scan resumed")

    def cancel(self) -> None:
        """Cancel the current scan."""
        with self._lock:
            if self._progress.state in (ScanState.RUNNING, ScanState.PAUSED):
                self._progress.state = ScanState.CANCELLED
                self._cancel_ev.set()
                self._pause_ev.set()   # unblock paused thread
                log.info("Scan cancellation requested")

    def get_status(self) -> dict:
        """Return current scan status dict. Thread-safe."""
        with self._lock:
            return self._progress.to_dict()

    # ── Scan thread ───────────────────────────────────────────────────────────

    def _run_scan(self, cols: int, rows: int, overlap: int,
                  pattern: str, objective: str, images_dir: str) -> None:
        """
        Background thread: drives the stage through the scan grid,
        captures a tile at each position.
        """
        try:
            from vyuhaa.scan_planners import get_scan_positions  # adjust import as needed
        except ImportError:
            # Fallback: simple raster
            def get_scan_positions(cols, rows, overlap, pattern):
                for r in range(rows):
                    row = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
                    for c in row:
                        yield c, r

        tile_w_px = 1280    # camera frame width in pixels
        tile_h_px = 720     # camera frame height in pixels
        # Convert pixels → stage steps (adjust scale for your optics)
        step_x = int(tile_w_px * (1 - overlap / 100))
        step_y = int(tile_h_px * (1 - overlap / 100))

        tile_index = 0
        start_pos = self._hw.get_position()

        for col, row in get_scan_positions(cols, rows, overlap, pattern):
            # ── Check for cancel ─────────────────────────────────────────
            if self._cancel_ev.is_set():
                break

            # ── Wait if paused ───────────────────────────────────────────
            self._pause_ev.wait()
            if self._cancel_ev.is_set():
                break

            # ── Move to tile position ────────────────────────────────────
            target_x = start_pos["x"] + col * step_x
            target_y = start_pos["y"] + row * step_y
            self._hw.move_absolute(x=target_x, y=target_y, z=start_pos["z"])

            # ── Autofocus every row ──────────────────────────────────────
            if col == 0 and self._hw.autofocus:
                try:
                    self._hw.run_autofocus()
                except Exception as e:
                    log.warning(f"Autofocus failed at ({col},{row}): {e}")

            # ── Capture tile ─────────────────────────────────────────────
            try:
                jpeg = self._hw.grab_jpeg_bytes()
                fname = os.path.join(images_dir, f"tile_{row:03d}_{col:03d}.jpg")
                with open(fname, "wb") as f:
                    f.write(jpeg)
            except Exception as e:
                log.error(f"Capture failed at tile ({col},{row}): {e}")

            # ── Update progress ──────────────────────────────────────────
            tile_index += 1
            with self._lock:
                self._progress.tile_index = tile_index

            if self.on_progress:
                try:
                    self.on_progress(self._progress)
                except Exception:
                    pass

        # ── Finish ───────────────────────────────────────────────────────
        with self._lock:
            if self._cancel_ev.is_set():
                self._progress.state = ScanState.CANCELLED
            else:
                self._progress.state = ScanState.COMPLETE
            self._progress.ended_at = time.time()
            final = self._progress

        log.info(f"Scan finished: state={final.state.name}  tiles={final.tile_index}/{final.total_tiles}")

        if self.on_complete:
            try:
                self.on_complete(final)
            except Exception:
                pass

    def _fail(self, message: str) -> None:
        with self._lock:
            self._progress.state = ScanState.ERROR
            self._progress.error = message
            self._progress.ended_at = time.time()
        log.error(f"Scan error: {message}")
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                pass
