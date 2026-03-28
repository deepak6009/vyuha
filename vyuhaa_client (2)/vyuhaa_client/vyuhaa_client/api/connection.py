"""
Vyuhaa Remote Client — Connection / Endpoint Manager
Handles WebSocket relay connections to the remote device agent.

Usage
-----
    mgr = ConnectionManager()
    mgr.relay_status_changed.connect(my_slot)
    mgr.device_status_changed.connect(my_slot)
    mgr.log_message.connect(log_slot)
    mgr.connect_relay(url, room, key)
    mgr.disconnect()
"""

from __future__ import annotations
import json
import time
from enum import Enum, auto
from PySide6.QtCore import QObject, Signal, QTimer, QThread, Slot
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtNetwork import QAbstractSocket


class RelayStatus(Enum):
    OFFLINE      = auto()
    CONNECTING   = auto()
    CONNECTED    = auto()  # relay OK, waiting for device
    LIVE         = auto()  # relay + device both reachable
    ERROR        = auto()


class DeviceStatus(Enum):
    OFFLINE    = auto()
    POLLING    = auto()
    ONLINE     = auto()
    ERROR      = auto()


# ---------------------------------------------------------------------------
# Endpoint paths (relative to the device agent base URL tunnelled via relay)
# ---------------------------------------------------------------------------
class Endpoints:
    # OpenFlexure / device agent REST-like commands sent as JSON over WS
    PING          = "ping"
    MOVE          = "move"           # {axis, steps}
    POSITION      = "position/get"  # → {x, y, z}
    AUTOFOCUS     = "autofocus"
    ZERO_COORDS   = "zero"
    CAPTURE       = "capture"        # → {image_b64}
    STREAM_START  = "stream/start"   # {quality}
    STREAM_STOP   = "stream/stop"
    SCAN_START    = "scan/start"     # {cols, rows, overlap, pattern, label, objective}
    SCAN_PAUSE    = "scan/pause"
    SCAN_RESUME   = "scan/resume"
    SCAN_CANCEL   = "scan/cancel"
    SCAN_STATUS   = "scan/status"    # → {tile_index, total, state}
    FILES_LIST    = "files/list"     # → [{name, size, type, date}, ...]
    FILES_DELETE  = "files/delete"   # {names: [...]}
    FILES_EXPORT  = "files/export"   # {names: [...]}  → download token
    SETTINGS_GET  = "settings/get"
    SETTINGS_SET  = "settings/set"   # {key: value, ...}


# ---------------------------------------------------------------------------
# Main connection manager
# ---------------------------------------------------------------------------
class ConnectionManager(QObject):
    """
    Signals
    -------
    relay_status_changed(RelayStatus)
    device_status_changed(DeviceStatus)
    log_message(str)                  — human-readable log line
    latency_updated(int)              — round-trip ms
    message_received(dict)            — parsed JSON from relay
    position_updated(float, float, float)  — x, y, z μm
    scan_progress(int, int)           — done_tiles, total_tiles
    files_updated(list)               — list of file-info dicts
    error_occurred(str)
    """

    relay_status_changed  = Signal(object)   # RelayStatus
    device_status_changed = Signal(object)   # DeviceStatus
    log_message           = Signal(str)
    latency_updated       = Signal(int)
    message_received      = Signal(dict)
    position_updated      = Signal(float, float, float)
    scan_progress         = Signal(int, int)
    files_updated         = Signal(list)
    error_occurred        = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ws          = QWebSocket(parent=self)
        self._relay_url   = ""
        self._room        = ""
        self._key         = ""
        self._auto_reconnect = True
        self._relay_status  = RelayStatus.OFFLINE
        self._device_status = DeviceStatus.OFFLINE
        self._ping_timer    = QTimer(self)
        self._reconnect_timer = QTimer(self)
        self._ping_sent_at  = 0.0
        self._msg_callbacks: dict[str, list] = {}  # cmd → [callable]

        # Wire WebSocket signals
        self._ws.connected.connect(self._on_ws_connected)
        self._ws.disconnected.connect(self._on_ws_disconnected)
        self._ws.textMessageReceived.connect(self._on_text_received)
        self._ws.errorOccurred.connect(self._on_ws_error)

        # Ping every 5 s once connected
        self._ping_timer.setInterval(5000)
        self._ping_timer.timeout.connect(self._send_ping)

        # Reconnect attempt every 4 s
        self._reconnect_timer.setInterval(4000)
        self._reconnect_timer.timeout.connect(self._attempt_reconnect)

    # ── Public API ─────────────────────────────────────────────────────────

    def connect_relay(self, url: str, room: str, key: str,
                      auto_reconnect: bool = True) -> None:
        """Begin connection to the relay server."""
        self._relay_url      = url
        self._room           = room
        self._key            = key
        self._auto_reconnect = auto_reconnect

        self._set_relay_status(RelayStatus.CONNECTING)
        self._set_device_status(DeviceStatus.OFFLINE)
        self.log_message.emit(f"Connecting → {url}  room={room}")

        from PySide6.QtCore import QUrl
        ws_url = QUrl(url)
        self._ws.open(ws_url)

    def disconnect(self) -> None:
        """Tear down relay + device connections."""
        self._auto_reconnect = False
        self._reconnect_timer.stop()
        self._ping_timer.stop()
        if self._ws.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._ws.close()
        self._set_relay_status(RelayStatus.OFFLINE)
        self._set_device_status(DeviceStatus.OFFLINE)
        self.log_message.emit("Disconnected.")

    def send_command(self, cmd: str, payload: dict | None = None,
                     callback=None) -> bool:
        """
        Send a JSON command to the remote device via relay.
        Returns False if relay is not connected.
        """
        if self._relay_status not in (RelayStatus.CONNECTED, RelayStatus.LIVE):
            return False
        msg = {"cmd": cmd, "room": self._room}
        if payload:
            msg["data"] = payload
        try:
            self._ws.sendTextMessage(json.dumps(msg))
        except Exception as e:
            self.error_occurred.emit(f"Send error: {e}")
            return False
        if callback:
            self._msg_callbacks.setdefault(cmd, []).append(callback)
        return True

    # Convenience wrappers ------------------------------------------------

    def cmd_move(self, axis: str, steps: float) -> None:
        self.send_command(Endpoints.MOVE, {"axis": axis, "steps": steps})

    def cmd_autofocus(self) -> None:
        self.send_command(Endpoints.AUTOFOCUS)

    def cmd_zero(self) -> None:
        self.send_command(Endpoints.ZERO_COORDS)

    def cmd_capture(self, callback=None) -> None:
        self.send_command(Endpoints.CAPTURE, callback=callback)

    def cmd_stream_start(self, quality: str = "HD") -> None:
        self.send_command(Endpoints.STREAM_START, {"quality": quality})

    def cmd_stream_stop(self) -> None:
        self.send_command(Endpoints.STREAM_STOP)

    def cmd_scan_start(self, cols: int, rows: int, overlap: int,
                       pattern: str, label: str, objective: str) -> None:
        self.send_command(Endpoints.SCAN_START, {
            "cols": cols, "rows": rows, "overlap": overlap,
            "pattern": pattern, "label": label, "objective": objective
        })

    def cmd_scan_pause(self) -> None:
        self.send_command(Endpoints.SCAN_PAUSE)

    def cmd_scan_resume(self) -> None:
        self.send_command(Endpoints.SCAN_RESUME)

    def cmd_scan_cancel(self) -> None:
        self.send_command(Endpoints.SCAN_CANCEL)

    def cmd_files_list(self) -> None:
        self.send_command(Endpoints.FILES_LIST)

    def cmd_files_delete(self, names: list[str]) -> None:
        self.send_command(Endpoints.FILES_DELETE, {"names": names})

    def cmd_files_export(self, names: list[str]) -> None:
        self.send_command(Endpoints.FILES_EXPORT, {"names": names})

    def cmd_settings_get(self) -> None:
        self.send_command(Endpoints.SETTINGS_GET)

    def cmd_settings_set(self, settings: dict) -> None:
        self.send_command(Endpoints.SETTINGS_SET, settings)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def relay_status(self) -> RelayStatus:
        return self._relay_status

    @property
    def device_status(self) -> DeviceStatus:
        return self._device_status

    @property
    def is_live(self) -> bool:
        return self._relay_status == RelayStatus.LIVE

    # ── Internal WebSocket callbacks ───────────────────────────────────────

    @Slot()
    def _on_ws_connected(self) -> None:
        self._set_relay_status(RelayStatus.CONNECTED)
        self.log_message.emit("Relay connected — authenticating…")
        # Send join/auth handshake
        auth_msg = {"type": "join", "room": self._room, "key": self._key}
        self._ws.sendTextMessage(json.dumps(auth_msg))
        self._ping_timer.start()
        self._send_ping()

    @Slot()
    def _on_ws_disconnected(self) -> None:
        self._ping_timer.stop()
        prev = self._relay_status
        self._set_relay_status(RelayStatus.OFFLINE)
        self._set_device_status(DeviceStatus.OFFLINE)
        if prev != RelayStatus.OFFLINE:
            self.log_message.emit("Relay connection dropped.")
        if self._auto_reconnect:
            self.log_message.emit("Auto-reconnect in 4 s…")
            self._reconnect_timer.start()

    @Slot(object)
    def _on_ws_error(self, error) -> None:
        msg = self._ws.errorString()
        self.log_message.emit(f"WS error: {msg}")
        self._set_relay_status(RelayStatus.ERROR)
        self.error_occurred.emit(msg)

    @Slot(str)
    def _on_text_received(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        self.message_received.emit(msg)
        msg_type = msg.get("type", "")
        cmd      = msg.get("cmd", "")

        # ── Protocol messages ──
        if msg_type == "joined":
            self.log_message.emit(f"Joined room '{self._room}' — looking for device agent…")
            self._set_device_status(DeviceStatus.POLLING)
            self.send_command(Endpoints.PING)

        elif msg_type == "pong":
            latency = int((time.time() - self._ping_sent_at) * 1000)
            self.latency_updated.emit(latency)

        elif msg_type == "device_online":
            self._set_relay_status(RelayStatus.LIVE)
            self._set_device_status(DeviceStatus.ONLINE)
            self.log_message.emit("Device agent online — hardware ready.")

        elif msg_type == "device_offline":
            self._set_relay_status(RelayStatus.CONNECTED)
            self._set_device_status(DeviceStatus.OFFLINE)
            self.log_message.emit("Device agent disconnected.")

        elif msg_type == "error":
            err = msg.get("message", "Unknown relay error")
            self.log_message.emit(f"Relay error: {err}")
            self.error_occurred.emit(err)

        # ── Data messages ──
        elif cmd == Endpoints.POSITION:
            d = msg.get("data", {})
            self.position_updated.emit(
                float(d.get("x", 0)),
                float(d.get("y", 0)),
                float(d.get("z", 0))
            )

        elif cmd == Endpoints.SCAN_STATUS:
            d = msg.get("data", {})
            self.scan_progress.emit(
                int(d.get("tile_index", 0)),
                int(d.get("total", 0))
            )

        elif cmd == Endpoints.FILES_LIST:
            files = msg.get("data", [])
            self.files_updated.emit(files)

        # Dispatch to registered one-shot callbacks
        if cmd and cmd in self._msg_callbacks:
            for cb in self._msg_callbacks.pop(cmd, []):
                try:
                    cb(msg.get("data"))
                except Exception:
                    pass

    # ── Helpers ────────────────────────────────────────────────────────────

    def _set_relay_status(self, status: RelayStatus) -> None:
        if status != self._relay_status:
            self._relay_status = status
            self.relay_status_changed.emit(status)

    def _set_device_status(self, status: DeviceStatus) -> None:
        if status != self._device_status:
            self._device_status = status
            self.device_status_changed.emit(status)

    def _send_ping(self) -> None:
        self._ping_sent_at = time.time()
        try:
            self._ws.sendTextMessage(json.dumps({"type": "ping"}))
        except Exception:
            pass

    @Slot()
    def _attempt_reconnect(self) -> None:
        if self._relay_status in (RelayStatus.OFFLINE, RelayStatus.ERROR):
            self._reconnect_timer.stop()
            self.log_message.emit("Attempting reconnect…")
            self.connect_relay(self._relay_url, self._room, self._key, True)


