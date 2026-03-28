"""
api_client.py — Endpoint connection manager for Vyuhaa Remote Client.

Handles:
  • WebSocket relay connection  (PySide6 QWebSocket)
  • Simulated device-agent handshake and hardware bridge
  • Stage move / capture / autofocus RPC messages
  • Scan tile sequencing
  • Latency heartbeat

All communication goes through AppState signals so pages stay decoupled.
"""
from __future__ import annotations
import json
import math
import random
import uuid

from PySide6.QtCore import QObject, QTimer, QUrl, Qt
from PySide6.QtNetwork import QSslConfiguration, QSslSocket, QAbstractSocket
try:
    from PySide6.QtWebSockets import QWebSocket, QWebSocketProtocol
    _HAS_WEBSOCKET = True
except ImportError:
    _HAS_WEBSOCKET = False

from api.app_state import AppState
from api.webrtc_client import WebRTCPeer


class RelayClient(QObject):
    """
    Manages the two-layer connection:
      Layer 1 → Relay Server (WebSocket / TLS)
      Layer 2 → Device Agent  (via relay room)
      Layer 3 → Microscope HW (USB/Serial bridge reported by agent)
    """

    def __init__(self, state: AppState, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._ws: "QWebSocket | None" = None
        self._manual_disconnect = False
        self._latency_timer = QTimer(self)
        self._latency_timer.setInterval(2000)
        self._latency_timer.timeout.connect(self._update_latency)
        self._conn_phase_timer = QTimer(self)
        self._conn_phase_timer.setSingleShot(True)

        # Scan step timer
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(320)
        self._scan_timer.timeout.connect(self._scan_step)
        self._scan_order: list[tuple[int, int]] = []
        self._scan_current_idx: int = 0

        # Relay proxy state
        self._relay_access_token: str = ""
        self._relay_username: str = ""
        self._relay_microscope_id: str = ""
        self._relay_target_microscope: str = ""
        self._relay_pos_timer: "QTimer | None" = None

        # Active chunked downloads: name → {dest, offset, total, fh}
        self._download_state: dict = {}

        # Frame watchdog — if no frame arrives for 8 s while connected, auto-recover
        self._frame_watchdog = QTimer(self)
        self._frame_watchdog.setSingleShot(True)
        self._frame_watchdog.setInterval(8000)
        self._frame_watchdog.timeout.connect(self._on_stream_timeout)

        # WebRTC P2P peer — bypasses relay for frames and commands when active
        self._webrtc = WebRTCPeer(self)
        # Force QueuedConnection: WebRTCPeer emits from a native threading.Thread (asyncio loop),
        # not a QThread. PySide6 can't auto-detect the thread boundary and defaults to
        # DirectConnection, causing slot execution in the asyncio thread. Queued forces
        # delivery via the Qt event loop so all slots run safely in the main thread.
        self._webrtc.offer_ready.connect(self._on_webrtc_offer_ready, Qt.QueuedConnection)
        self._webrtc.ice_ready.connect(self._on_webrtc_ice_ready, Qt.QueuedConnection)
        self._webrtc.frame_received.connect(self._on_p2p_frame, Qt.QueuedConnection)
        self._webrtc.command_response.connect(self._handle_proxy_response, Qt.QueuedConnection)
        self._webrtc.connected.connect(self._on_p2p_connected, Qt.QueuedConnection)
        self._webrtc.disconnected.connect(self._on_p2p_disconnected, Qt.QueuedConnection)
        if self._webrtc.available:
            self._webrtc.start()

    def _parse_relay_identity(self, raw_value: str) -> tuple[str, str]:
        """
        Backward-compatible parser for the connect field.

        Supported formats:
          - "username"                          -> username only
          - "username@microscope-id"            -> explicit target microscope
          - "username|microscope-id"            -> explicit target microscope
        """
        text = (raw_value or "").strip()
        if "@" in text:
            user, target = text.split("@", 1)
            return user.strip(), target.strip()
        if "|" in text:
            user, target = text.split("|", 1)
            return user.strip(), target.strip()
        return text, ""

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Begin the phased connection sequence."""
        s = self.state
        if s.relay_status not in ("off", "disconnected"):
            return

        self._manual_disconnect = False

        s.relay_status = "connecting"
        s.relay_state_changed.emit("connecting")
        s.log_message.emit(f"→ Connecting to {s.relay_url}…")

        if _HAS_WEBSOCKET:
            self._ws_connect()
        else:
            # Simulate connection (no QtWebSockets available / server offline)
            self._simulate_phase1()

    def _is_relay_mode(self) -> bool:
        """Relay mode = an access key / password is provided (requires login)."""
        return bool(self.state.relay_key.strip())

    def _ws_connect(self) -> None:
        """Attempt a real WebSocket connection; fall back to simulation on error."""
        self._ws = QWebSocket("", QWebSocketProtocol.Version13, self)
        if hasattr(self._ws, "setReadBufferSize"):
            self._ws.setReadBufferSize(256 * 1024)
        # Route connected signal depending on direct-server vs relay mode
        if self._is_relay_mode():
            self._ws.connected.connect(self._on_relay_connected)
        else:
            self._ws.connected.connect(self._on_ws_connected)
        self._ws.disconnected.connect(self._on_ws_disconnected)
        self._ws.errorOccurred.connect(self._on_ws_error)
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.binaryMessageReceived.connect(self._on_binary_message)
        url = QUrl(self.state.relay_url)
        # Apply SSL config for secure connections (wss:// or ngrok/Tailscale)
        if url.scheme() in ("wss", "https"):
            ssl_cfg = QSslConfiguration.defaultConfiguration()
            ssl_cfg.setPeerVerifyMode(QSslSocket.VerifyNone)  # accept self-signed certs
            self._ws.setSslConfiguration(ssl_cfg)
        self.state.log_message.emit(f"→ Opening {self.state.relay_url}…")
        self._ws.open(url)

    def _on_ws_connected(self) -> None:
        # Real server connected — skip simulated handshake, go live immediately
        print("[SYNC-CLIENT] _on_ws_connected (direct local WS mode)")
        s = self.state
        s.relay_status = "connected"
        s.relay_state_changed.emit("connected")
        s.device_status = "live"
        s.device_state_changed.emit("live")
        s.log_message.emit("✓ Connected to Vyuhaa Microscope server.")
        self._latency_timer.start()

    def _on_relay_connected(self) -> None:
        """Connected to relay — send login with username (room) + password (key)."""
        print("[SYNC-CLIENT] _on_relay_connected (relay mode)")
        s = self.state
        self._relay_username, self._relay_target_microscope = self._parse_relay_identity(s.relay_room)
        if not self._relay_username:
            s.log_message.emit("✕ Relay username is empty.")
            self.disconnect()
            return
        s.log_message.emit(f"✓ Relay server reached — logging in as '{self._relay_username}'…")
        self._relay_access_token = ""
        self._relay_microscope_id = ""
        self._ws.sendTextMessage(json.dumps({
            "type":     "login",
            "username": self._relay_username,
            "password": s.relay_key,
        }))

    def _on_ws_error(self, error) -> None:
        if self._manual_disconnect:
            return
        self.state.log_message.emit(f"⚠ WebSocket error ({error}).")
        self.disconnect()
        if self.state.auto_reconnect:
            self.state.log_message.emit("↻ Connection lost — auto-reconnecting…")
            QTimer.singleShot(3000, self.connect)

    def _on_ws_disconnected(self) -> None:
        if self._manual_disconnect:
            return
        if self.state.relay_status in ("connected", "connecting"):
            self.disconnect()
            if self.state.auto_reconnect:
                self.state.log_message.emit("↻ Connection lost — auto-reconnecting…")
                QTimer.singleShot(3000, self.connect)

    def _on_binary_message(self, data) -> None:
        """Handle binary WebSocket frame — raw JPEG bytes pushed by the microscope."""
        jpeg = bytes(data)
        if jpeg:
            self.state.frame_received.emit(jpeg)
            self._mark_device_live()
            self._reset_frame_watchdog()

    def _on_message(self, raw: str) -> None:
        """Handle incoming JSON-RPC messages from the relay/agent."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        msg_type = msg.get("type", "")
        method   = msg.get("method", "")
        params   = msg.get("params", {})
        if msg_type not in ("stream_frame",):
            print(f"[SYNC-CLIENT] _on_message type='{msg_type}' method='{method}' keys={list(msg.keys())}")
        # ── WebRTC signaling ──────────────────────────────────────────────
        if msg_type == "answer":
            self._webrtc.set_remote_description(msg.get("sdp", ""), "answer")
            return

        if msg_type == "ice":
            candidate = msg.get("candidate")
            if candidate:
                self._webrtc.add_ice_candidate(candidate)
            return

        # ── Real server frame / position ──────────────────────────────────
        # Local WS → binary WebSocket frames handled by _on_binary_message.
        # Relay fallback: server sends {"type": "stream_frame", "jpeg": b64} when P2P is down.
        if msg_type == "stream_frame":
            import base64 as _b64
            jpeg_b64 = msg.get("jpeg", "")
            if jpeg_b64:
                try:
                    self.state.frame_received.emit(_b64.b64decode(jpeg_b64))
                    self._mark_device_live()
                    self._reset_frame_watchdog()
                except Exception:
                    pass
            return

        if msg_type == "position":
            self._mark_device_live()
            self.state.pos_x = float(msg.get("x", self.state.pos_x))
            self.state.pos_y = float(msg.get("y", self.state.pos_y))
            self.state.pos_z = float(msg.get("z", self.state.pos_z))
            self.state.position_changed.emit(
                self.state.pos_x, self.state.pos_y, self.state.pos_z
            )
            if "step_size" in msg:
                self.state.set_step_size(float(msg.get("step_size", self.state.step_size)))
            return

        if msg_type == "step_size":
            self._mark_device_live()
            self.state.set_step_size(float(msg.get("value", self.state.step_size)))
            return

        # ── Relay login / proxy responses ─────────────────────────────────
        if msg_type == "login_success":
            self._relay_access_token = msg.get("access_token", "")
            s = self.state
            s.relay_status = "connected"
            s.relay_state_changed.emit("connected")
            microscopes = msg.get("microscopes", [])
            if not microscopes:
                s.device_status = "searching"
                s.device_state_changed.emit("searching")
                self.state.log_message.emit("⚠ Relay login OK but no microscopes available.")
                return
            online = [m for m in microscopes if m.get("online")]
            target = None
            if self._relay_target_microscope:
                target = next(
                    (m for m in microscopes if m.get("microscope_id") == self._relay_target_microscope and m.get("online")),
                    None,
                )
                if target is None:
                    self.state.log_message.emit(
                        f"⚠ Requested microscope '{self._relay_target_microscope}' not online; using first available."
                    )
            if target is None:
                target = online[0] if online else microscopes[0]
            self._relay_microscope_id = target.get("microscope_id", "")
            target_online = bool(target.get("online"))
            target_busy = bool(target.get("busy"))

            if target_online and not target_busy:
                s.device_status = "searching"
                s.device_state_changed.emit("searching")
                s.log_message.emit(
                    f"✓ Relay connected — reaching microscope '{self._relay_microscope_id}'…"
                )
                self._start_relay_polling()
            elif target_online and target_busy:
                s.device_status = "found"
                s.device_state_changed.emit("found")
                s.log_message.emit(
                    f"⚠ Microscope '{self._relay_microscope_id}' is online but busy."
                )
                self._stop_relay_polling()
            else:
                s.device_status = "searching"
                s.device_state_changed.emit("searching")
                s.log_message.emit(
                    f"⚠ Microscope '{self._relay_microscope_id}' is not online yet."
                )
                self._stop_relay_polling()

            self._latency_timer.start()
            return

        if msg_type == "proxy_response":
            self._handle_proxy_response(msg)
            return

        if msg_type == "state_sync":
            self._handle_state_sync(msg.get("state", {}))
            return

        if msg_type == "error":
            err = str(msg.get("message", "unknown"))
            self.state.log_message.emit(f"✕ Relay error: {err}")
            low = err.lower()
            if "not online" in low:
                self._stop_relay_polling()
                self.state.device_status = "searching"
                self.state.device_state_changed.emit("searching")
            elif "busy" in low:
                self._stop_relay_polling()
                self.state.device_status = "found"
                self.state.device_state_changed.emit("found")
            return

        # ── Relay / simulation messages ───────────────────────────────────
        if method == "agent.connected":
            self._phase3_agent_ok()
        elif method == "hardware.ready":
            self._phase4_live()
        elif method == "stage.position":
            self.state.pos_x = params.get("x", self.state.pos_x)
            self.state.pos_y = params.get("y", self.state.pos_y)
            self.state.pos_z = params.get("z", self.state.pos_z)
            self.state.position_changed.emit(
                self.state.pos_x, self.state.pos_y, self.state.pos_z
            )
        elif method == "autofocus.result":
            score = params.get("score", 450)
            grade = "EXCELLENT" if score > 500 else "GOOD" if score > 380 else "FAIR"
            self.state.focus_updated.emit(score, grade)

    # ── Relay proxy helpers ───────────────────────────────────────────────────

    def _start_relay_polling(self) -> None:
        """Fetch initial position once, then let server push updates via state_sync."""
        self._stop_relay_polling()
        # One-shot fetch — server pushes position after every move so continuous polling is unnecessary
        QTimer.singleShot(300, self._relay_poll_position)

        # Kick off WebRTC handshake — once DataChannel opens, relay is bypassed for frames
        if self._webrtc.available:
            self._webrtc.create_offer()

    # ── WebRTC P2P handlers ───────────────────────────────────────────────────

    def _on_webrtc_offer_ready(self, sdp: str) -> None:
        """Send SDP offer to relay so it can be forwarded to the microscope."""
        if not self._ws or not self._ws.isValid():
            return
        self._ws.sendTextMessage(json.dumps({
            "type":          "offer",
            "access_token":  self._relay_access_token,
            "microscope_id": self._relay_microscope_id,
            "sdp":           sdp,
        }))
        self.state.log_message.emit("→ WebRTC offer sent — negotiating P2P connection…")

    def _on_webrtc_ice_ready(self, candidate: dict) -> None:
        """Forward ICE candidate to relay."""
        if not self._ws or not self._ws.isValid():
            return
        self._ws.sendTextMessage(json.dumps({
            "type":          "ice",
            "access_token":  self._relay_access_token,
            "microscope_id": self._relay_microscope_id,
            "candidate":     candidate,
        }))

    def _on_p2p_frame(self, jpeg: bytes) -> None:
        """Frame arrived directly via DataChannel — no relay in path."""
        print(f"[SYNC-CLIENT] _on_p2p_frame {len(jpeg)} bytes", flush=True)
        self.state.frame_received.emit(jpeg)
        self._mark_device_live()
        self._reset_frame_watchdog()

    def _on_p2p_connected(self) -> None:
        self.state.log_message.emit("✓ P2P active — frames and commands bypass relay")
        # Server pushes position via state_sync after every move — polling is redundant
        self._stop_relay_polling()

    def _on_p2p_disconnected(self) -> None:
        self.state.log_message.emit("⚠ P2P disconnected — attempting stream recovery in 3 s…")
        # Schedule renegotiation — relay is still up so we can redo the WebRTC handshake
        QTimer.singleShot(3000, self._try_restore_stream)

    def _stop_relay_polling(self) -> None:
        if self._relay_pos_timer:
            self._relay_pos_timer.stop()
            self._relay_pos_timer = None

    def _reset_frame_watchdog(self) -> None:
        """Restart the 8-second frame-absence watchdog on every received frame."""
        if self.state.device_status == "live":
            self._frame_watchdog.start()   # start() restarts automatically if already running

    def _on_stream_timeout(self) -> None:
        """Watchdog fired — no frames for 8 s while connected. Attempt recovery."""
        if self.state.device_status != "live":
            return
        self.state.log_message.emit("⚠ Stream timeout — no frames for 8 s, recovering…")
        self._try_restore_stream()

    def _try_restore_stream(self) -> None:
        """Restore frames: re-negotiate WebRTC P2P, or fall back to relay-proxy request."""
        if self.state.device_status != "live":
            return
        if self._webrtc.available:
            self.state.log_message.emit("↻ Renegotiating P2P stream…")
            self._webrtc.create_offer()
        elif self._ws and self._ws.isValid():
            # Local WS — server loop is self-healing; ping it to confirm the link is alive
            self._ws.sendTextMessage(json.dumps({"method": "stream.restart"}))
        # Watchdog restarts automatically when next frame arrives

    def _mark_device_live(self) -> None:
        """Mark device as live only after real device traffic is received."""
        if self.state.device_status != "live":
            self.state.device_status = "live"
            self.state.device_state_changed.emit("live")
            if self._relay_microscope_id:
                self.state.log_message.emit(
                    f"✓ Relay live — microscope '{self._relay_microscope_id}'."
                )

    def _relay_send_command(self, action: str, params: "dict | None" = None) -> str:
        """Send a command — via P2P DataChannel if available, relay otherwise."""
        # Prefer direct P2P: no relay hop, no proxy overhead
        if self._webrtc.p2p_active:
            return self._webrtc.send_command(action, params)

        # Relay fallback
        if not self._ws or not self._ws.isValid():
            return ""
        cmd_id = uuid.uuid4().hex[:8]
        self._ws.sendTextMessage(json.dumps({
            "type":          "proxy_command",
            "access_token":  self._relay_access_token,
            "microscope_id": self._relay_microscope_id,
            "action":        action,
            "params":        params or {},
            "cmd_id":        cmd_id,
        }))
        return cmd_id

    def _relay_poll_position(self) -> None:
        if self._relay_microscope_id:
            self._relay_send_command("get_position")

    def _handle_proxy_response(self, msg: dict) -> None:
        """Dispatch a proxy_response from the relay or DataChannel to the appropriate state update."""
        # state_sync messages can arrive via WebRTC DataChannel text path
        if msg.get("type") == "state_sync":
            print(f"[SYNC-CLIENT] _handle_proxy_response routing state_sync from DataChannel: kind='{msg.get('state', {}).get('kind')}'")
            self._handle_state_sync(msg.get("state", {}))
            return
        action = msg.get("action", "")
        if msg.get("status") != "ok":
            return
        self._mark_device_live()
        if action == "capture":
            try:
                import base64
                self.state.frame_received.emit(base64.b64decode(msg["image"]))
            except Exception:
                pass
        elif action in ("get_position", "move", "zero"):
            pos = msg.get("position", {})
            if pos:
                self.state.pos_x = float(pos.get("x", self.state.pos_x))
                self.state.pos_y = float(pos.get("y", self.state.pos_y))
                self.state.pos_z = float(pos.get("z", self.state.pos_z))
                self.state.position_changed.emit(
                    self.state.pos_x, self.state.pos_y, self.state.pos_z
                )
            if "step_size" in msg:
                self.state.set_step_size(float(msg.get("step_size", self.state.step_size)))
        elif action == "move_to":
            pos = msg.get("position", {})
            if pos:
                self.state.pos_x = float(pos.get("x", self.state.pos_x))
                self.state.pos_y = float(pos.get("y", self.state.pos_y))
                self.state.pos_z = float(pos.get("z", self.state.pos_z))
                self.state.position_changed.emit(
                    self.state.pos_x, self.state.pos_y, self.state.pos_z
                )
        elif action == "set_step_size":
            self.state.set_step_size(float(msg.get("step_size", self.state.step_size)))
        elif action == "autofocus":
            res = msg.get("autofocus_result", {})
            sharpness = res.get("sharpness", [])
            score = int(max(sharpness) * 1000) if sharpness else 450
            grade = "EXCELLENT" if score > 500 else "GOOD" if score > 380 else "FAIR"
            self.state.focus_updated.emit(score, grade)
        elif action == "files_list":
            files = msg.get("files", [])
            self.state.files_list_received.emit(files)
        elif action == "file_download":
            self._handle_download_chunk(msg)

    def _handle_state_sync(self, state: dict) -> None:
        """Handle one-way state push from microscope server via relay."""
        self._mark_device_live()
        kind = state.get("kind", "")
        print(f"[SYNC-CLIENT] _handle_state_sync kind='{kind}' state={state}")

        if kind == "position":
            self.state.pos_x = float(state.get("x", self.state.pos_x))
            self.state.pos_y = float(state.get("y", self.state.pos_y))
            self.state.pos_z = float(state.get("z", self.state.pos_z))
            print(f"[SYNC-CLIENT] position_changed emitting x={self.state.pos_x} y={self.state.pos_y} z={self.state.pos_z}")
            self.state.position_changed.emit(
                self.state.pos_x, self.state.pos_y, self.state.pos_z
            )
            return

        if kind == "step_size":
            self.state.set_step_size(float(state.get("value", self.state.step_size)))
            return

        if kind == "navigate":
            page = str(state.get("page", "")).strip().lower()
            # Map server page names → client page names
            server_to_client = {"manual": "live", "cal": "calibration"}
            client_page = server_to_client.get(page, page)
            print(f"[SYNC-CLIENT] navigate → emitting '{client_page}' (raw='{page}')")
            if client_page:
                self.state.navigate.emit(client_page)
            return

        if kind == "focus":
            score = int(state.get("score", 450))
            grade = str(state.get("grade", "GOOD"))
            self.state.focus_updated.emit(score, grade)
            return

        if kind == "scan_progress":
            done  = int(state.get("done", 0))
            total = int(state.get("total", 1))
            self.state.scan_current = done
            self.state.scan_total   = total
            # Stop local simulation timer — real server progress takes over
            if self._scan_timer.isActive():
                self._scan_timer.stop()
            self.state.scan_progress_changed.emit(done, total)
            return

        if kind == "scan_state":
            scan_state = str(state.get("state", "idle"))
            # Map server state names to client names
            _map = {"scanning": "running", "cancelled": "idle", "done": "done"}
            client_state = _map.get(scan_state, scan_state)
            self.state.scan_state = client_state
            self.state.scan_state_changed.emit(client_state)
            if client_state in ("idle", "done"):
                self._scan_timer.stop()
            return

    # ── Simulated phases (used when server unavailable) ───────────────────────

    def _simulate_phase1(self) -> None:
        """Relay connecting… → connected after 1.4 s."""
        QTimer.singleShot(1400, self._phase2_relay_ok)

    def _phase2_relay_ok(self) -> None:
        if self._manual_disconnect or self.state.relay_status == "off":
            return
        s = self.state
        s.relay_status = "connected"
        s.relay_state_changed.emit("connected")
        s.device_status = "searching"
        s.device_state_changed.emit("searching")
        s.log_message.emit(f"✓ Relay connected. Waiting for device agent {s.relay_room}…")
        QTimer.singleShot(1400, self._phase3_agent_ok)

    def _phase3_agent_ok(self) -> None:
        if self._manual_disconnect or self.state.relay_status == "off":
            return
        s = self.state
        s.device_status = "found"
        s.device_state_changed.emit("found")
        s.log_message.emit("✓ Device agent found. Bridging to microscope hardware…")
        QTimer.singleShot(1800, self._phase4_live)

    def _phase4_live(self) -> None:
        if self._manual_disconnect or self.state.relay_status == "off":
            return
        s = self.state
        s.device_status = "live"
        s.device_state_changed.emit("live")
        s.log_message.emit("✓ All systems live. Operator Arun ready on-site.")
        self._latency_timer.start()

    def disconnect(self) -> None:
        """Tear down the connection and reset state."""
        self._manual_disconnect = True
        self._latency_timer.stop()
        self._frame_watchdog.stop()
        self._stop_relay_polling()
        self._webrtc.stop()
        self._relay_access_token = ""
        self._relay_microscope_id = ""
        if self._ws:
            self._ws.close()
            self._ws = None
        s = self.state
        s.relay_status = "off"
        s.device_status = "offline"
        s.relay_state_changed.emit("off")
        s.device_state_changed.emit("offline")
        s.log_message.emit("Disconnected from relay.")

    # ── Latency ──────────────────────────────────────────────────────────────

    def _update_latency(self) -> None:
        if _HAS_WEBSOCKET and self._ws and self._ws.isValid():
            self._ws.ping()  # real ping; pong handler would update latency
        latency = random.randint(10, 32)
        self.state.latency_ms = latency
        self.state.latency_updated.emit(latency)

    # ── Stage RPC ─────────────────────────────────────────────────────────────

    def send_move(self, axis: str, direction: int) -> None:
        """Send a stage-move command. Server will broadcast authoritative position back."""
        if self._ws and self._ws.isValid():
            if self._is_relay_mode() and self._relay_microscope_id:
                self._relay_send_command(
                    "move", {axis: direction * int(self.state.step_size * 100)}
                )
            else:
                self._ws.sendTextMessage(json.dumps({
                    "method": "stage.move",
                    "params": {"axis": axis, "direction": direction,
                               "step": self.state.step_size}
                }))

    def send_set_position(self, x: float, y: float, z: float) -> None:
        """Set absolute stage coordinates; server returns authoritative position."""
        if self._ws and self._ws.isValid():
            if self._is_relay_mode() and self._relay_microscope_id:
                self._relay_send_command("move_to", {
                    "x": int(round(float(x))),
                    "y": int(round(float(y))),
                    "z": int(round(float(z))),
                })
            else:
                self._ws.sendTextMessage(json.dumps({
                    "method": "stage.goto",
                    "params": {"x": float(x), "y": float(y), "z": float(z)}
                }))

    def send_set_step_size(self, step_um: float) -> None:
        """Update move step size and sync it to server/other clients."""
        self.state.set_step_size(float(step_um))
        if self._ws and self._ws.isValid():
            if self._is_relay_mode() and self._relay_microscope_id:
                self._relay_send_command("set_step_size", {"value": float(step_um)})
            else:
                self._ws.sendTextMessage(json.dumps({
                    "method": "stage.step.set",
                    "params": {"value": float(step_um)}
                }))

    def send_autofocus(self) -> None:
        if self._ws and self._ws.isValid():
            if self._is_relay_mode() and self._relay_microscope_id:
                self._relay_send_command("autofocus")
            else:
                self._ws.sendTextMessage(json.dumps({"method": "autofocus.run"}))
        else:
            # Simulate
            score = random.randint(390, 550)
            grade = "EXCELLENT" if score > 500 else "GOOD" if score > 380 else "FAIR"
            QTimer.singleShot(800, lambda: self.state.focus_updated.emit(score, grade))

    def send_capture(self) -> None:
        if self._ws and self._ws.isValid():
            if self._is_relay_mode() and self._relay_microscope_id:
                self._relay_send_command("capture")
            else:
                self._ws.sendTextMessage(json.dumps({"method": "camera.capture"}))
        self.state.capture_triggered.emit()

    def send_stream_toggle(self, active: bool) -> None:
        self.state.streaming = active
        self.state.stream_toggled.emit(active)
        if self._ws and self._ws.isValid():
            self._ws.sendTextMessage(json.dumps({
                "method": "stream.toggle", "params": {"active": active}
            }))

    def send_quality(self, quality: str) -> None:
        self.state.quality = quality
        self.state.quality_changed.emit(quality)
        if self._ws and self._ws.isValid():
            self._ws.sendTextMessage(json.dumps({
                "method": "stream.quality", "params": {"quality": quality}
            }))

    def send_navigate(self, screen: str) -> None:
        """Sync page navigation to the server UI (all pages)."""
        # Map client page names → server page names
        page_map = {
            "home":     "home",
            "connect":  "connect",
            "live":     "manual",   # server calls it "manual"
            "scan":     "scan",
            "files":    "files",
            "settings": "settings",
        }
        target = page_map.get(screen)
        if not target:
            return

        if self._ws and self._ws.isValid():
            if self._is_relay_mode() and self._relay_microscope_id:
                self._relay_send_command("navigate", {"page": target})
            else:
                self._ws.sendTextMessage(json.dumps({
                    "method": "ui.navigate",
                    "params": {"page": target}
                }))

    # ── Files ─────────────────────────────────────────────────────────────────

    def get_files(self) -> None:
        """Request file list from server. Result arrives via files_list_received signal on AppState."""
        self._relay_send_command("files_list")

    def download_file(self, name: str, dest_path: str) -> None:
        """
        Start a chunked download of *name* to *dest_path* on the client machine.
        Progress is reported via AppState.download_progress(name, bytes_done, bytes_total).
        """
        import os
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        self._download_state[name] = {"dest": dest_path, "offset": 0, "total": -1, "fh": open(dest_path, "wb")}
        self._relay_send_command("file_download", {"name": name, "offset": 0})

    def _handle_download_chunk(self, msg: dict) -> None:
        import base64
        name   = msg.get("name", "")
        offset = int(msg.get("offset", 0))
        total  = int(msg.get("total", 0))
        done   = bool(msg.get("done", False))
        data_b64 = msg.get("data", "")
        state = self._download_state.get(name)
        if not state:
            return
        if state["total"] < 0:
            state["total"] = total
        chunk = base64.b64decode(data_b64) if data_b64 else b""
        fh = state.get("fh")
        if fh:
            fh.write(chunk)
        new_offset = offset + len(chunk)
        self.state.download_progress.emit(name, new_offset, total)
        if done:
            if fh:
                fh.close()
            self._download_state.pop(name, None)
            self.state.download_complete.emit(name, state["dest"])
        else:
            # Request next chunk
            self._relay_send_command("file_download", {"name": name, "offset": new_offset})

    # ── Scan ─────────────────────────────────────────────────────────────────

    @staticmethod
    def compute_scan_order(cols: int, rows: int, pattern: str) -> list[tuple[int, int]]:
        """Return ordered list of (row, col) tuples for the given pattern."""
        tiles: list[tuple[int, int]] = []
        if pattern == "raster":
            for r in range(rows):
                for c in range(cols):
                    tiles.append((r, c))
        elif pattern == "snake":
            for r in range(rows):
                cols_range = range(cols) if r % 2 == 0 else range(cols - 1, -1, -1)
                for c in cols_range:
                    tiles.append((r, c))
        elif pattern == "spiral":
            top, bot, left, right = 0, rows - 1, 0, cols - 1
            while top <= bot and left <= right:
                for c in range(left, right + 1):
                    tiles.append((top, c))
                top += 1
                for r in range(top, bot + 1):
                    tiles.append((r, right))
                right -= 1
                if top <= bot:
                    for c in range(right, left - 1, -1):
                        tiles.append((bot, c))
                    bot -= 1
                if left <= right:
                    for r in range(bot, top - 1, -1):
                        tiles.append((r, left))
                    left += 1
        return tiles

    @staticmethod
    def compute_scan_stats(cols: int, rows: int, overlap_pct: int,
                            objective: str) -> dict:
        fov_mm = 0.22  # default 40× FOV
        if "20×" in objective:
            fov_mm = 0.44
        elif "10×" in objective:
            fov_mm = 0.88
        step = fov_mm * (1 - overlap_pct / 100)
        area_w = (cols - 1) * step + fov_mm
        area_h = (rows - 1) * step + fov_mm
        area = area_w * area_h
        total = cols * rows
        secs = total * 4
        size_mb = round(total * 6.2)
        return {
            "total": total,
            "time_str": f"{secs // 60} m {secs % 60} s",
            "area_str": f"{area:.2f} mm²",
            "size_str": f"{size_mb} MB",
        }

    def start_scan(self) -> None:
        s = self.state
        if s.scan_state == "running":
            return
        s.scan_total = s.scan_cols * s.scan_rows
        s.scan_current = 0
        s.scan_state = "running"
        self._scan_order = self.compute_scan_order(
            s.scan_cols, s.scan_rows, s.scan_pattern
        )
        self._scan_current_idx = 0
        s.scan_state_changed.emit("running")
        self._scan_timer.start()

    def pause_scan(self) -> None:
        s = self.state
        if s.scan_state == "running":
            self._scan_timer.stop()
            s.scan_state = "paused"
            s.scan_state_changed.emit("paused")
        elif s.scan_state == "paused":
            s.scan_state = "running"
            s.scan_state_changed.emit("running")
            self._scan_timer.start()

    def stop_scan(self) -> None:
        self._scan_timer.stop()
        self.state.scan_state = "idle"
        self.state.scan_state_changed.emit("idle")

    def reset_scan(self) -> None:
        self.stop_scan()
        self.state.scan_current = 0
        self.state.scan_progress_changed.emit(0, self.state.scan_total)

    def _scan_step(self) -> None:
        s = self.state
        idx = self._scan_current_idx
        if s.scan_state != "running" or idx >= len(self._scan_order):
            self._scan_timer.stop()
            return

        # Mark previous tile done
        if idx > 0:
            pr, pc = self._scan_order[idx - 1]
            s.scan_tile_updated.emit(pr, pc, "done")

        r, c = self._scan_order[idx]
        s.scan_tile_updated.emit(r, c, "current")
        self._scan_current_idx += 1
        s.scan_current = self._scan_current_idx
        s.scan_progress_changed.emit(s.scan_current, s.scan_total)

        if self._ws and self._ws.isValid():
            self._ws.sendTextMessage(json.dumps({
                "method": "scan.move_to_tile",
                "params": {"row": r, "col": c, "index": idx}
            }))

        if self._scan_current_idx >= len(self._scan_order):
            self._scan_timer.stop()
            # Mark last tile done
            lr, lc = self._scan_order[-1]
            s.scan_tile_updated.emit(lr, lc, "done")
            s.scan_state = "done"
            s.scan_state_changed.emit("done")
