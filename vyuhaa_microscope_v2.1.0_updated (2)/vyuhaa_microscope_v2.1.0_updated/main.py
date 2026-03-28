"""
main.py
───────
Vyuhaa Microscope — PySide6 desktop application.

Combines the Vyuhaa-app UI design with the full Jetson backend:
  • TopBar + BottomBar + 7-page stack (Vyuhaa-app design)
  • Optional local WebSocket server on :8765 (remote Vyuhaa client)
  • Optional relay+WebRTC remote sharing (RemoteSharingService)
  • Direct hardware via Vyuhaa_api / hardware_registry

Run:
    python main.py                          # auto-detects hardware
    VYUHAA_SIMULATION=1 python main.py      # force simulation
    python main.py --simulation
"""

from __future__ import annotations
import sys
import os
import asyncio
import base64
import json
import logging
import threading
import platform

import os as _os

def _load_dotenv() -> None:
    """Load .env BEFORE any hardware module is imported — Vyuhaa_api reads
    HARDWARE_TYPE at module level the instant it is imported."""
    _env_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env")
    if not _os.path.exists(_env_path):
        return
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _os.environ.setdefault(_key.strip(), _val.strip().strip('"').strip("'"))
    # print(f"Loaded .env from {_env_path}")

_load_dotenv()  # must run before Vyuhaa_api is imported

# print("DEBUG: Pre-Import Vyuhaa_api")
import Vyuhaa_api  # import early — wires hardware detection + MockInterface
# print("DEBUG: Post-Import Vyuhaa_api")

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFrame, QMessageBox,
)
from PySide6.QtCore import Qt, QTimer, Slot, Signal
from PySide6.QtGui import QFont

import theme
from pages.home_page        import HomePage
from pages.connect_page     import ConnectPage
from pages.manual_move_page import ManualMovePage
from pages.scan_page        import ScanPage
from pages.calibration_page import CalibrationPage
from pages.settings_page    import SettingsPage
from pages.files_page       import FilesPage

from hardware_registry import hardware
from scan_manager      import ScanManager
from remote_sharing    import RemoteSharingService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")
# print("DEBUG: Logging initialized")


# ─────────────────────────────────────────────────────────────────────────────
# Local WebSocket server (streams frames to Vyuhaa remote client on :8765)
# ─────────────────────────────────────────────────────────────────────────────

class LocalWebSocketServer:
    """Streams JPEG frames and handles commands from the local Vyuhaa client."""

    PORT = 8765

    def __init__(self, hw_registry, on_step_size=None, on_position=None, on_navigate=None):
        self._hw = hw_registry
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._step_size_um: float = 10.0
        self._on_step_size = on_step_size
        self._on_position = on_position
        self._on_navigate = on_navigate
        self._get_initial_states = None   # set after MainWindow is built
        self._clients = set()
        self._ws_loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="local-ws"
        )
        self._thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ws_loop = loop
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:
            log.error(f"LocalWebSocketServer: {exc}")
        finally:
            self._ws_loop = None

    async def _serve(self) -> None:
        try:
            import websockets
        except ImportError:
            log.warning("websockets not installed — local WS server disabled")
            return
        import socket
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.bind(("0.0.0.0", self.PORT))
        raw.setblocking(False)
        async with websockets.serve(self._handle, sock=raw):
            try:
                lan_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                lan_ip = "<your-lan-ip>"
            log.info("=" * 60)
            log.info(f"  WS server listening on port {self.PORT}")
            log.info(f"  Same machine  : ws://localhost:{self.PORT}")
            log.info(f"  Same Wi-Fi    : ws://{lan_ip}:{self.PORT}")
            log.info("=" * 60)
            await asyncio.Future()

    async def _handle(self, ws) -> None:
        log.info("Vyuhaa client connected")
        self._clients.add(ws)
        # Immediately tell the new client which page the server is on
        if self._get_initial_states:
            initial = self._get_initial_states()
            print(f"[SYNC] local WS: new client → sending {len(initial)} initial state(s): {initial}")
            for state in initial:
                try:
                    await ws.send(json.dumps({"type": "state_sync", "state": state}))
                    print(f"[SYNC] local WS: sent initial state {state}")
                except Exception as e:
                    print(f"[SYNC] local WS: failed to send initial state {state}: {e}")
        loop = asyncio.get_event_loop()
        frame_task = asyncio.create_task(self._stream_frames(ws, loop))
        pos_task   = asyncio.create_task(self._stream_position(ws, loop))
        try:
            async for raw in ws:
                try:
                    await self._dispatch(ws, json.loads(raw), loop)
                except Exception as e:
                    log.debug(f"Dispatch error: {e}")
        except Exception:
            pass
        finally:
            self._clients.discard(ws)
            frame_task.cancel()
            pos_task.cancel()
            log.info("Vyuhaa client disconnected")

    async def _stream_frames(self, ws, loop) -> None:
        """Send raw JPEG frames at ~20 fps as binary WebSocket messages."""
        import base64
        consecutive_errors = 0
        while True:
            try:
                if self._hw.is_ready():
                    b64 = await loop.run_in_executor(None, self._hw.grab_jpeg_b64)
                    await ws.send(base64.b64decode(b64))   # binary frame — no JSON overhead
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(0.5)
                    continue
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors <= 3:
                    log.warning(f"_stream_frames error: {e}")
                if consecutive_errors > 20:
                    # WS is likely broken — exit so _handle closes the connection cleanly
                    log.error("_stream_frames: persistent send failure, closing client connection")
                    break
            await asyncio.sleep(0.05)

    async def _stream_position(self, ws, loop) -> None:
        """Send stage position at 2 Hz."""
        while True:
            try:
                if self._hw.is_ready():
                    pos = await loop.run_in_executor(None, self._hw.get_position)
                    await ws.send(json.dumps({"type": "position", **pos}))
            except Exception as e:
                log.debug(f"_stream_position error: {e}")
            await asyncio.sleep(0.5)

    async def _dispatch(self, ws, msg: dict, loop) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})
        if method == "stage.move":
            axis  = params.get("axis", "x")
            direc = int(params.get("direction", 1))
            steps = int(float(params.get("step", 10)) * 100)
            await loop.run_in_executor(
                None, lambda: self._hw.move_relative(**{axis: direc * steps})
            )
        elif method == "autofocus.run":
            try:
                result = await loop.run_in_executor(
                    None, lambda: self._hw.run_autofocus(dz=10000)
                )
                sharpness = result.get("sharpness", [])
                best = int(max(sharpness) * 1000) if sharpness else 450
            except Exception:
                best = 450
            await ws.send(json.dumps(
                {"method": "autofocus.result", "params": {"score": best}}
            ))
        elif method == "camera.capture":
            try:
                b64 = await loop.run_in_executor(None, self._hw.grab_jpeg_b64)
                await ws.send(json.dumps({"type": "frame", "jpeg": b64}))
            except Exception:
                pass

        elif method == "stage.step.set":
            step = float(params.get("value", 10.0))
            self._step_size_um = step
            if self._on_step_size:
                self._on_step_size(step)
            # Echo back to all connected clients
            self.broadcast_state({"kind": "step_size", "value": step})

        elif method == "stream.restart":
            # Stream loop is continuous — just acknowledge; frames will resume on next cycle
            log.info("Client requested stream restart — frame loop is self-healing")

    def broadcast_state(self, state: dict) -> None:
        """Push a state_sync message to all connected local WS clients. Thread-safe."""
        if not self._clients or not self._ws_loop:
            return
        payload = json.dumps({"type": "state_sync", "state": state})

        async def _send_all() -> None:
            dead = set()
            for client_ws in list(self._clients):
                try:
                    await client_ws.send(payload)
                except Exception:
                    dead.add(client_ws)
            self._clients -= dead

        try:
            asyncio.run_coroutine_threadsafe(_send_all(), self._ws_loop)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Top bar
# ─────────────────────────────────────────────────────────────────────────────

class TopBar(QFrame):
    close_requested    = Signal()
    minimize_requested = Signal()
    maximize_requested = Signal()
    home_requested     = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.BAR_HEIGHT)
        self.setStyleSheet(
            f"QFrame{{background:{theme.SURFACE};border-bottom:1px solid {theme.BORDER};}}"
        )
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0) # No top margin
        layout.setSpacing(8)

        # Clickable Logo
        logo_btn = QPushButton("🔬")
        logo_btn.setFixedSize(theme.LOGO_SIZE, theme.LOGO_SIZE)
        logo_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.TEAL}; border-radius:6px; font-size:20px; border:none;
            }}
            QPushButton:hover {{ background:#25d4c2; }}
        """)
        logo_btn.clicked.connect(self.home_requested.emit)

        app_name = QLabel("Vyuhaa")
        app_name.setStyleSheet(
            f"font-family:'Syne',sans-serif;font-size:16px;font-weight:700;"
            f"color:{theme.WHITE};background:transparent;"
        )

        layout.addWidget(logo_btn)
        layout.addWidget(app_name)
        layout.addStretch()

        self.hw_pill    = QLabel()
        self.relay_pill = QLabel()
        self._set_hw_pill(connected=False)
        self._set_relay_pill(active=False)

        layout.addWidget(self.hw_pill)
        layout.addWidget(self.relay_pill)


        # Control Buttons
        btn_style = f"""
            QPushButton {{
                background:transparent; color:{theme.TEXT_DIM}; 
                border:1px solid {theme.BORDER}; border-radius:4px;
                font-size:12px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{theme.SURFACE2}; color:{theme.TEXT}; }}
        """

        # Minimize
        self.min_btn = QPushButton("—")
        self.min_btn.setFixedSize(26, 26)
        self.min_btn.setStyleSheet(btn_style)
        self.min_btn.clicked.connect(self.minimize_requested.emit)
        layout.addWidget(self.min_btn)

        # Maximize/Restore
        self.max_btn = QPushButton("◻")
        self.max_btn.setFixedSize(26, 26)
        self.max_btn.setStyleSheet(btn_style)
        self.max_btn.clicked.connect(self.maximize_requested.emit)
        layout.addWidget(self.max_btn)

        # Close
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{theme.TEXT_DIM}; 
                border:1px solid {theme.BORDER}; border-radius:4px;
                font-size:12px; font-weight:bold;
            }}
            QPushButton:hover {{ background:{theme.RED}; color:{theme.WHITE}; border-color:{theme.RED}; }}
        """)
        self.close_btn.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.close_btn)

    def _pill_style(self, border: str, text_colour: str) -> str:
        return f"""
            QLabel {{
                background:{theme.SURFACE2}; border:1px solid {border};
                border-radius:14px; padding:4px 12px;
                font-family:'JetBrains Mono',monospace;
                font-size:12px; font-weight:bold;
                letter-spacing:0.02em; color:{text_colour};
            }}
        """

    def _set_hw_pill(self, connected: bool):
        dot    = "🟢" if connected else "🔴"
        label  = "HW: CONNECTED" if connected else "HW: DISCONNECTED"
        border = "rgba(34,197,94,0.35)" if connected else theme.BORDER
        colour = theme.TEXT if connected else theme.TEXT_MID
        self.hw_pill.setText(f"{dot}  {label}")
        self.hw_pill.setStyleSheet(self._pill_style(border, colour))

    def _set_relay_pill(self, active: bool):
        dot    = "🟦" if active else "⚫"
        label  = "RELAY: ACTIVE" if active else "RELAY: OFF"
        border = "rgba(30,184,168,0.35)" if active else theme.BORDER
        colour = theme.TEAL if active else theme.TEXT_MID
        self.relay_pill.setText(f"{dot}  {label}")
        self.relay_pill.setStyleSheet(self._pill_style(border, colour))


# ─────────────────────────────────────────────────────────────────────────────
# Bottom bar
# ─────────────────────────────────────────────────────────────────────────────

class BottomBar(QFrame):
    from PySide6.QtCore import Signal
    home_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(theme.BAR_HEIGHT)
        self.setStyleSheet(
            f"QFrame{{background:{theme.SURFACE};border-top:1px solid {theme.BORDER};}}"
        )
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        self.ctx_manual = QLabel()
        self.ctx_manual.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace;font-size:8px;"
            f"color:{theme.TEXT_DIM};background:transparent;"
        )
        self.ctx_manual.hide()

        self.ctx_settings = QLabel("v2.1.0 · Vyuhaa Enterprise AI Systems")
        self.ctx_settings.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace;font-size:10px;"
            f"color:{theme.TEXT_DIM};background:transparent;"
        )
        self.ctx_settings.hide()

        layout.addWidget(self.ctx_manual)
        layout.addWidget(self.ctx_settings)
        layout.addStretch()

        self.home_btn = QPushButton("◀  Home")
        self.home_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.TEAL};color:#000;border:none;border-radius:10px;
                padding:6px 20px;font-family:'Syne',sans-serif;
                font-weight:700;font-size:13px;
            }}
            QPushButton:hover {{ background:#25d4c2; }}
            QPushButton:pressed {{ background:#18a090; }}
        """)
        self.home_btn.clicked.connect(self.home_clicked)
        layout.addWidget(self.home_btn)

    def _set_hw_pill(self, connected: bool):
        t, c = ("HW ONLINE", theme.TEAL) if connected else ("HW OFFLINE", theme.TEXT_DIM)
        self.hw_pill.setText(t)
        self.hw_pill.setStyleSheet(
            f"font-family:'JetBrains Mono'; font-size:9px; font-weight:700; "
            f"color:{c}; border:1px solid {c}; border-radius:10px; padding:2px 10px;"
        )

    def _set_relay_pill(self, active: bool):
        t, c = ("RELAY ON", theme.TEAL) if active else ("RELAY OFF", theme.TEXT_DIM)
        self.relay_pill.setText(t)
        self.relay_pill.setStyleSheet(
            f"font-family:'JetBrains Mono'; font-size:9px; font-weight:700; "
            f"color:{c}; border:1px solid {c}; border-radius:10px; padding:2px 10px;"
        )

    def set_page(self, page_id: str, step_size: float = 10.0):
        on_home = (page_id == "home")
        self.home_btn.setVisible(not on_home)
        self.ctx_manual.setVisible(page_id == "manual")
        self.ctx_settings.setVisible(page_id == "settings")
        if page_id == "manual":
            unit = "1 mm" if step_size >= 1000 else f"{step_size:.0f} μm"
            self.ctx_manual.setText(f"STEP: {unit}")

    def update_step(self, step_size: float):
        unit = "1 mm" if step_size >= 1000 else f"{step_size:.0f} μm"
        self.ctx_manual.setText(f"STEP: {unit}")


# ─────────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self, sharing_service: RemoteSharingService, local_ws: "LocalWebSocketServer"):
        super().__init__()
        self._sharing = sharing_service
        self._local_ws = local_ws
        self._current_page = "home"   # tracks the active page for newly connecting clients
        self.setWindowTitle("Vyuhaa Microscope")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint) # Clean look for touch
        
        # Set exact size (HDMI targeting)
        self.setFixedSize(800, 480)
        
        import platform
        if platform.system() != "Windows":
            self.showFullScreen() # Fullscreen on Jetson, but restricted to 800x480
        self._build()
        self._wire_sharing_signals()

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        # Clean edge-to-edge layout now that 800x480 resolution is strictly locked
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.topbar = TopBar()
        root.addWidget(self.topbar)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}

        self.home_page     = HomePage()
        self.connect_page  = ConnectPage()
        self.manual_page   = ManualMovePage()
        self.scan_page     = ScanPage()
        self.cal_page      = CalibrationPage()
        self.files_page    = FilesPage()
        self.settings_page = SettingsPage()

        for pid, page in [
            ("home",     self.home_page),
            ("connect",  self.connect_page),
            ("manual",   self.manual_page),
            ("scan",     self.scan_page),
            ("cal",      self.cal_page),
            ("files",    self.files_page),
            ("settings", self.settings_page),
        ]:
            self.stack.addWidget(page)
            self.pages[pid] = page

        root.addWidget(self.stack, stretch=1)

        self.bottombar = BottomBar()
        root.addWidget(self.bottombar)

        self._wire_nav_signals()
        self.goto_page("home")

    def _wire_nav_signals(self):
        self.home_page.navigate.connect(self.goto_page)
        self.files_page.navigate.connect(self.goto_page)
        
        # Header/Footer connects
        self.bottombar.home_clicked.connect(lambda: self.goto_page("home"))
        self.topbar.home_requested.connect(lambda: self.goto_page("home"))
        
        self.topbar.minimize_requested.connect(self.showMinimized)
        self.topbar.maximize_requested.connect(self._toggle_fullscreen)
        self.topbar.close_requested.connect(self.close)

        self.connect_page.hw_connected.connect(self._on_hw_connected)
        self.connect_page.hw_disconnected.connect(self._on_hw_disconnected)
        self.connect_page.relay_connected.connect(self._on_relay_connected)
        self.connect_page.relay_disconnected.connect(self._on_relay_disconnected)

    def _wire_sharing_signals(self):
        self._sharing.client_connected.connect(self._on_sharing_client)
        self._sharing.client_disconnected.connect(self._on_sharing_client_gone)
        self._sharing.status_changed.connect(
            lambda msg: log.info(f"Relay: {msg}")
        )
        self._sharing.error_occurred.connect(
            lambda err: log.error(f"Relay error: {err}")
        )
        # Remote client requesting a page change → navigate server UI
        self._sharing.navigate_requested.connect(self.goto_page)
        # Remote client changing step size → update server manual page
        self._sharing.step_size_requested.connect(self.manual_page.set_step_remote)
        # Remote client moving stage → update server position display
        self._sharing.position_requested.connect(self.manual_page.set_position_remote)
        # Local server position changes → push to relay AND local WS clients
        self.manual_page.position_changed.connect(
            lambda x, y, z: self._sharing.broadcast_state(
                {"kind": "position", "x": x, "y": y, "z": z}
            )
        )
        self.manual_page.position_changed.connect(
            lambda x, y, z: self._local_ws.broadcast_state(
                {"kind": "position", "x": x, "y": y, "z": z}
            )
        )
        # Local server step size changes → push to relay AND local WS clients
        self.manual_page.step_changed.connect(
            lambda step: self._sharing.broadcast_state(
                {"kind": "step_size", "value": step}
            )
        )
        self.manual_page.step_changed.connect(
            lambda step: self._local_ws.broadcast_state(
                {"kind": "step_size", "value": step}
            )
        )
        # Scan progress/state → push to relay AND local WS clients
        self.scan_page.scan_state_broadcast.connect(
            lambda state: self._sharing.broadcast_state({"kind": "scan_state", "state": state})
        )
        self.scan_page.scan_state_broadcast.connect(
            lambda state: self._local_ws.broadcast_state({"kind": "scan_state", "state": state})
        )
        self.scan_page.scan_progress_broadcast.connect(
            lambda done, total: self._sharing.broadcast_state(
                {"kind": "scan_progress", "done": done, "total": total}
            )
        )
        self.scan_page.scan_progress_broadcast.connect(
            lambda done, total: self._local_ws.broadcast_state(
                {"kind": "scan_progress", "done": done, "total": total}
            )
        )

    # ── Navigation ────────────────────────────────────────────────────────────

    def goto_page(self, page_id: str):
        if page_id not in self.pages:
            return
        self._current_page = page_id   # keep track for newly connecting clients
        prev = self.stack.currentWidget()
        if hasattr(prev, "on_page_deactivated"):
            prev.on_page_deactivated()
        self.stack.setCurrentWidget(self.pages[page_id])
        self.bottombar.set_page(
            page_id, getattr(self.manual_page, "_step", 10.0)
        )
        nxt = self.pages[page_id]
        if hasattr(nxt, "on_page_activated"):
            nxt.on_page_activated()
        # Notify remote clients of the page change (using client-friendly page names)
        server_to_client = {"manual": "live", "cal": "calibration"}
        client_page = server_to_client.get(page_id, page_id)
        nav_state = {"kind": "navigate", "page": client_page}
        print(f"[SYNC] goto_page({page_id}) → broadcasting navigate='{client_page}' "
              f"relay_active={self._sharing._active} "
              f"relay_ws={self._sharing._relay_ws is not None} "
              f"local_ws_clients={len(self._local_ws._clients)}")
        self._sharing.broadcast_state(nav_state)
        self._local_ws.broadcast_state(nav_state)

    @Slot()
    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ── HW / relay state ─────────────────────────────────────────────────────

    @Slot(object)
    def _on_hw_connected(self, device):
        self.topbar._set_hw_pill(connected=True)
        self.home_page.update_connect_badge("CONNECTED", "connected")
        # Populate hardware_registry so local WS server can use it
        import Vyuhaa_api as ofa
        hardware.camera    = ofa._camera
        hardware.stage     = ofa._stage
        hardware.autofocus = ofa._autofocus
        if hardware.stage and hardware.camera:
            sm = ScanManager(hardware)
            hardware.scan_manager = sm

    @Slot()
    def _on_hw_disconnected(self):
        self.topbar._set_hw_pill(connected=False)
        self.home_page.update_connect_badge("DISCONNECTED", "disconnected")
        hardware.camera = hardware.stage = hardware.autofocus = None

    @Slot(str)
    def _on_relay_connected(self, room: str):
        self.topbar._set_relay_pill(active=True)
        self.home_page.update_connect_badge("RELAY ON", "relay")
        # Also start the RemoteSharingService if env vars are configured
        self._sharing.start()

    @Slot()
    def _on_relay_disconnected(self):
        self.topbar._set_relay_pill(active=False)
        self._sharing.stop()
        connected = getattr(self.connect_page, "_hw_connected", False)
        self.home_page.update_connect_badge(
            "CONNECTED" if connected else "DISCONNECTED",
            "connected" if connected else "disconnected",
        )

    @Slot(str)
    def _on_sharing_client(self, username: str):
        log.info(f"Remote viewer connected: {username}")
        server_to_client = {"manual": "live", "cal": "calibration"}
        client_page = server_to_client.get(self._current_page, self._current_page)
        step = getattr(self.manual_page, "_step", 10.0)
        print(f"[SYNC] _on_sharing_client({username!r}) → pushing page='{client_page}' step={step}")
        self._sharing.broadcast_state({"kind": "navigate",  "page": client_page})
        self._sharing.broadcast_state({"kind": "step_size", "value": step})

    def _get_initial_states(self) -> list[dict]:
        """Return state dicts to send to a freshly connected local WS client."""
        server_to_client = {"manual": "live", "cal": "calibration"}
        client_page = server_to_client.get(self._current_page, self._current_page)
        step = getattr(self.manual_page, "_step", 10.0)
        return [
            {"kind": "navigate",  "page": client_page},
            {"kind": "step_size", "value": step},
        ]

    @Slot(str)
    def _on_sharing_client_gone(self, username: str):
        log.info(f"Remote viewer disconnected: {username}")

    def closeEvent(self, event):
        prev = self.stack.currentWidget()
        if hasattr(prev, "on_page_deactivated"):
            prev.on_page_deactivated()
        self._sharing.stop()
        import Vyuhaa_api as ofa
        ofa.disconnect()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
# .env loader
# ─────────────────────────────────────────────────────────────────────────────

# _load_dotenv() is defined and called at module top, before Vyuhaa_api import


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # print("DEBUG: main() started")

    app = QApplication(sys.argv)
    app.setApplicationName("Vyuhaa")
    app.setOrganizationName("Vyuhaa Enterprise AI Systems")
    app.setStyleSheet(theme.GLOBAL_QSS)

    QFont.insertSubstitution("Syne", "Segoe UI")
    QFont.insertSubstitution("JetBrains Mono", "Consolas")

    # Optional FastAPI web server (OF Connect compatibility)
    if os.environ.get("ENABLE_WEB_UI") == "1":
        from server import start_web_server_background
        start_web_server_background(hardware)

    sharing_service = RemoteSharingService(hardware)

    # Auto-start relay disabled as per user request
    # _relay_url     = os.environ.get("RELAY_URL", "")
    # _microscope_id = os.environ.get("MICROSCOPE_ID", "")
    # _api_key       = os.environ.get("API_KEY", "")
    # if _relay_url and _microscope_id and _api_key:
    #     log.info(f"Auto-starting relay: {_relay_url} as '{_microscope_id}'")
    #     sharing_service.start()

    # Build window first so callbacks can reference its pages
    local_ws = LocalWebSocketServer(hardware)
    window = MainWindow(sharing_service, local_ws)
    # Wire step-size callback from local WS clients → manual page
    local_ws._on_step_size = window.manual_page.set_step_remote
    # Wire initial-state callback so new WS clients land on the correct page
    local_ws._get_initial_states = window._get_initial_states

    # Start local WebSocket server for Vyuhaa remote client
    local_ws.start()

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
