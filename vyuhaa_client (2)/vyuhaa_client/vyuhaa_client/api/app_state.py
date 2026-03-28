"""
app_state.py — Centralised application state for Vyuhaa Remote Client.
All pages read/write state through this singleton so that UI and logic stay decoupled.
"""
from __future__ import annotations
from PySide6.QtCore import QObject, Signal


class AppState(QObject):
    # ── Connection ───────────────────────────────────────────────────────────
    relay_state_changed = Signal(str)    # 'off' | 'connecting' | 'connected'
    device_state_changed = Signal(str)   # 'offline' | 'searching' | 'found' | 'live'
    latency_updated = Signal(int)        # ms
    log_message = Signal(str)            # plain-text log line

    # ── Stage ────────────────────────────────────────────────────────────────
    position_changed = Signal(float, float, float)   # x, y, z  (µm)
    step_size_changed = Signal(float)                # µm
    focus_updated = Signal(int, str)                 # score, grade

    # ── Live view ────────────────────────────────────────────────────────────
    stream_toggled = Signal(bool)
    recording_toggled = Signal(bool)
    capture_triggered = Signal()
    quality_changed = Signal(str)        # 'SD' | 'HD' | '4K'
    frame_received = Signal(bytes)       # raw JPEG bytes from server

    # ── Scan ─────────────────────────────────────────────────────────────────
    scan_state_changed = Signal(str)     # 'idle' | 'running' | 'paused' | 'done'
    scan_tile_updated = Signal(int, int, str)   # row, col, state: 'pending'|'current'|'done'
    scan_progress_changed = Signal(int, int)    # current, total

    # ── Operator chat ────────────────────────────────────────────────────────
    operator_message = Signal(str, str)  # who ('OPR'|'YOU'), text

    # ── Files ────────────────────────────────────────────────────────────────
    files_list_received = Signal(list)           # list of file dicts from server
    download_progress   = Signal(str, int, int)  # name, bytes_done, bytes_total
    download_complete   = Signal(str, str)        # name, local_dest_path

    # ── Navigation ───────────────────────────────────────────────────────────
    navigate = Signal(str)               # screen id

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Connection
        self.relay_status: str = "off"        # 'off'|'connecting'|'connected'
        self.device_status: str = "offline"   # 'offline'|'searching'|'found'|'live'
        self.latency_ms: int = 0
        self.relay_url: str = "ws://44.200.4.55:8765"
        self.relay_room: str = "admin"
        self.relay_key: str = "vyuhaa2026"
        self.auto_reconnect: bool = True

        # Stage
        self.pos_x: float = 142.5
        self.pos_y: float = -88.0
        self.pos_z: float = 10.0
        self.step_size: float = 10.0

        # Live view
        self.streaming: bool = True
        self.recording: bool = False
        self.quality: str = "HD"
        self.fps: int = 30

        # Scan
        self.scan_cols: int = 8
        self.scan_rows: int = 6
        self.scan_overlap: int = 15
        self.scan_label: str = "Slide_A"
        self.scan_pattern: str = "raster"  # 'raster'|'snake'|'spiral'
        self.scan_state: str = "idle"
        self.scan_current: int = 0
        self.scan_total: int = 48
        self.scan_objective: str = "40× (FOV 220 μm)"

        # Settings
        self.default_step: str = "10 μm"
        self.invert_x: bool = False
        self.invert_y: bool = False
        self.stage_speed: str = "Normal (1×)"
        self.autofocus_on_tile: bool = True

    # ── Helpers ──────────────────────────────────────────────────────────────
    def move_stage(self, axis: str, direction: int) -> None:
        """Apply a step move and emit position_changed."""
        delta = direction * self.step_size
        if axis == "x":
            self.pos_x = round(self.pos_x + delta, 1)
        elif axis == "y":
            self.pos_y = round(self.pos_y + delta, 1)
        elif axis == "z":
            self.pos_z = round(self.pos_z + delta, 1)
        self.position_changed.emit(self.pos_x, self.pos_y, self.pos_z)

    def zero_coords(self) -> None:
        self.pos_x = self.pos_y = self.pos_z = 0.0
        self.position_changed.emit(0.0, 0.0, 0.0)

    def set_step_size(self, step_um: float) -> None:
        self.step_size = float(step_um)
        self.step_size_changed.emit(self.step_size)
