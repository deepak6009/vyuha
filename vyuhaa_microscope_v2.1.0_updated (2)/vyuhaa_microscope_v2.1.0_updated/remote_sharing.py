"""
remote_sharing.py — On-demand remote sharing service for Vyuhaa Jetson.

Activated by the operator pressing "Share" in the PySide6 UI.
Connects outbound to the relay server, waits for a remote viewer,
negotiates a WebRTC P2P DataChannel, then handles all commands
entirely peer-to-peer — the relay sees no medical data.

Architecture:
  - Runs in a dedicated background thread with its own asyncio event loop.
  - Hardware access goes through HardwareRegistry (thread-safe).
  - PySide6 signals are emitted to notify the operator UI.

Relay protocol used:
  register_microscope / registered
  incoming_offer / answer / ice
  proxy_command (fallback if WebRTC P2P fails)
  ping / pong heartbeat

WebRTC flow:
  1. Relay forwards incoming_offer from remote client.
  2. Agent creates RTCPeerConnection, sets remote description, creates answer.
  3. ICE candidates exchanged via relay.
  4. Once DataChannel opens, relay is out of the command loop.
  5. All further commands arrive as DataChannel messages.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
from typing import Dict, Optional, Set

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("remote_sharing")

# ── Optional deps — only needed when sharing is started ──────────────────────
try:
    import websockets
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False
    log.warning("websockets not installed — remote sharing unavailable")

# WebRTC is optional — proxy_command fallback works without it
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCConfiguration, RTCIceServer
    _WEBRTC_OK = True
except ImportError:
    _WEBRTC_OK = False
    RTCPeerConnection = RTCSessionDescription = RTCIceCandidate = RTCConfiguration = RTCIceServer = None
    log.info("aiortc not installed — WebRTC P2P disabled, proxy_command fallback active")

STUN_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
    # Add TURN for hospital symmetric NAT:
    # {"urls": "turn:your-turn.vyuhaa.com:3478",
    #  "username": "user", "credential": "pass"},
]

HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "10"))
RECONNECT_DELAY    = int(os.environ.get("RECONNECT_DELAY",    "5"))
FRAME_PUSH_INTERVAL = float(os.environ.get("FRAME_PUSH_INTERVAL", "0.05"))  # 20 fps default
STREAM_JPEG_QUALITY = int(os.environ.get("STREAM_JPEG_QUALITY", "55"))


class RemoteSharingService(QObject):
    """
    PySide6-compatible service that manages the relay + WebRTC connection.

    Usage:
        service = RemoteSharingService(hardware)
        service.client_connected.connect(on_connected)
        service.client_disconnected.connect(on_disconnected)
        service.status_changed.connect(on_status)

        # Operator presses Share:
        service.start()

        # Operator presses Stop Sharing:
        service.stop()
    """

    # ── Qt signals ────────────────────────────────────────────────────────────
    client_connected    = Signal(str)   # username of remote viewer
    client_disconnected = Signal(str)
    status_changed      = Signal(str)   # human-readable status message
    error_occurred      = Signal(str)
    navigate_requested  = Signal(str)   # page id requested by remote client
    step_size_requested = Signal(float)
    position_requested  = Signal(float, float, float)

    def __init__(self, hardware_registry, parent=None) -> None:
        super().__init__(parent)
        self._hw      = hardware_registry
        self._active  = False
        self._thread: Optional[threading.Thread] = None
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._stop_ev: Optional[asyncio.Event] = None
        self._viewer_count: int = 0   # clients currently streaming via relay
        self._relay_ws = None
        self._active_channels: list = []   # open WebRTC DataChannels (P2P sessions)
        self._p2p_session_count: int = 0   # number of active P2P sessions

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        """Start the relay bridge. Idempotent — safe to call if already running."""
        if self._active:
            return
        if not _DEPS_OK:
            self.error_occurred.emit("Remote sharing requires: websockets, aiortc")
            return

        self._active = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="relay-bridge")
        self._thread.start()
        log.info("RemoteSharingService started")

    def stop(self) -> None:
        """Stop the relay bridge and disconnect all remote clients."""
        if not self._active:
            return
        self._active = False
        if self._loop and self._stop_ev:
            self._loop.call_soon_threadsafe(self._stop_ev.set)
        if self._thread:
            self._thread.join(timeout=8)
        self._thread = None
        self._loop   = None
        self._relay_ws = None
        log.info("RemoteSharingService stopped")

    def broadcast_state(self, state: dict) -> None:
        """
        Push a state_sync event to currently connected remote client(s).
        Prefers WebRTC DataChannel (P2P, zero relay hops) when available.
        Falls back to relay WebSocket for proxy_command clients.
        Thread-safe: can be called from UI thread.
        """
        if not self._active:
            print(f"[SYNC] broadcast_state DROPPED — sharing not active | state={state}")
            return
        if not self._loop:
            print(f"[SYNC] broadcast_state DROPPED — no event loop | state={state}")
            return

        payload = json.dumps({"type": "state_sync", "state": state})

        # ── Prefer P2P DataChannel — no relay involved ────────────────────
        if self._active_channels:
            print(f"[SYNC] broadcast_state → P2P ({len(self._active_channels)} ch) | state={state}")

            async def _send_via_channels():
                for ch in list(self._active_channels):
                    try:
                        ch.send(payload)
                        print(f"[SYNC] broadcast_state P2P sent OK | state={state}")
                    except Exception as e:
                        log.warning(f"DataChannel state send failed: {e}")

            try:
                asyncio.run_coroutine_threadsafe(_send_via_channels(), self._loop)
            except Exception as e:
                print(f"[SYNC] broadcast_state P2P schedule ERROR: {e}")
            return

        # ── Relay fallback — for proxy_command clients with no P2P ────────
        if not self._relay_ws:
            print(f"[SYNC] broadcast_state DROPPED — relay_ws is None and no P2P | state={state}")
            return

        print(f"[SYNC] broadcast_state → relay (no P2P) | state={state}")

        async def _send_state():
            try:
                await self._relay_ws.send(payload)
                print(f"[SYNC] broadcast_state relay sent OK | state={state}")
            except Exception as e:
                print(f"[SYNC] broadcast_state SEND ERROR: {e} | state={state}")

        try:
            asyncio.run_coroutine_threadsafe(_send_state(), self._loop)
        except Exception as e:
            print(f"[SYNC] broadcast_state run_coroutine_threadsafe ERROR: {e}")

    # ── Thread entry ──────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._loop    = asyncio.new_event_loop()
        self._stop_ev = asyncio.Event()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._bridge())
        except Exception as exc:
            log.exception(f"Relay bridge fatal error: {exc}")
        finally:
            if self._loop is not None:
                self._loop.close()

    # ── Main bridge coroutine ─────────────────────────────────────────────────

    async def _bridge(self) -> None:
        relay_url     = os.environ.get("RELAY_URL", "")
        microscope_id = os.environ.get("MICROSCOPE_ID", "")
        api_key       = os.environ.get("API_KEY", "")

        if not relay_url or not microscope_id or not api_key:
            msg = "RELAY_URL / MICROSCOPE_ID / API_KEY env vars not set"
            self.error_occurred.emit(msg)
            log.error(msg)
            return

        while self._active:
            try:
                async with websockets.connect(
                    relay_url,
                    compression   = None,
                    ping_interval = None,          # server sends WS pings
                    ping_timeout  = None,
                    max_size      = 10 * 1024 * 1024,
                    max_queue     = 1,
                    open_timeout  = 30,
                ) as ws:
                    self._relay_ws = ws
                    self._viewer_count = 0   # reset on each fresh connection

                    # ── Register ──────────────────────────────────────────
                    await ws.send(json.dumps({
                        "type":          "register_microscope",
                        "microscope_id": microscope_id,
                        "api_key":       api_key,
                    }))
                    resp = json.loads(await ws.recv())
                    if resp.get("type") == "error":
                        msg = f"Registration failed: {resp.get('message')}"
                        self.error_occurred.emit(msg)
                        log.error(msg)
                        await asyncio.sleep(RECONNECT_DELAY)
                        continue

                    self.status_changed.emit("Ready — waiting for remote viewer")
                    log.info(f"Registered as '{microscope_id}' on relay")

                    # ── Background tasks ──────────────────────────────────
                    tasks: Set[asyncio.Task] = set()
                    active_pcs: Dict[str, RTCPeerConnection] = {}

                    def _spawn(coro):
                        t = asyncio.create_task(coro)
                        tasks.add(t)
                        t.add_done_callback(tasks.discard)
                        return t

                    hb = asyncio.create_task(self._heartbeat(ws))
                    # Proactively push frames to relay at 20fps
                    frame_push = asyncio.create_task(self._push_frames(ws))

                    # ── Receive loop ──────────────────────────────────────
                    async for raw in ws:
                        if not self._active:
                            break
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        mtype = msg.get("type", "")

                        if mtype == "pong":
                            pass

                        elif mtype == "viewer_joined":
                            self._viewer_count += 1
                            username = msg.get("username", "remote viewer")
                            log.info(f"Viewer joined ({username}) — starting frame push ({self._viewer_count} active)")
                            print(f"[SYNC] viewer_joined({username!r}) — emitting client_connected")
                            # Notify the UI immediately so it can broadcast current state to the client
                            self.client_connected.emit(username)
                            self.status_changed.emit(f"Remote viewer connected: {username}")

                        elif mtype == "viewer_left":
                            self._viewer_count = max(0, self._viewer_count - 1)
                            log.info(f"Viewer left — {self._viewer_count} still active")

                        elif mtype == "incoming_offer":
                            if _WEBRTC_OK:
                                _spawn(self._handle_webrtc_session(ws, msg, active_pcs, _spawn))
                            else:
                                log.info("WebRTC offer ignored (aiortc not installed) — client will use proxy_command fallback")

                        elif mtype == "ice":
                            sid = msg.get("session_id", "")
                            pc  = active_pcs.get(sid)
                            if pc:
                                _spawn(self._add_ice(pc, msg))

                        elif mtype == "session_ended":
                            sid      = msg.get("session_id", "")
                            username = msg.get("from_user", "remote viewer")
                            pc       = active_pcs.pop(sid, None)
                            if pc:
                                _spawn(pc.close())
                            self.client_disconnected.emit(username)
                            self.status_changed.emit("Remote viewer disconnected — waiting for next")

                        elif mtype == "proxy_command":
                            # Fallback path: WebRTC failed, commands come via relay
                            _spawn(self._handle_proxy_fallback(ws, msg))

                        else:
                            log.debug(f"Unhandled relay message: {mtype}")

                    hb.cancel()
                    frame_push.cancel()
                    for t in list(tasks):
                        t.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    self._relay_ws = None

            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                if self._active:
                    self.status_changed.emit(f"Relay disconnected — reconnecting in {RECONNECT_DELAY}s")
                    log.warning(f"Relay disconnected: {exc}")
                    await asyncio.sleep(RECONNECT_DELAY)
            except Exception as exc:
                if self._active:
                    log.exception(f"Bridge error: {exc}")
                    await asyncio.sleep(RECONNECT_DELAY)

    # ── WebRTC session ────────────────────────────────────────────────────────

    async def _handle_webrtc_session(
        self,
        ws,
        msg: dict,
        active_pcs: Dict[str, RTCPeerConnection],
        spawn_fn,
    ) -> None:
        """
        Set up a WebRTC peer connection for one remote client.
        After the DataChannel opens, the relay is completely out of the loop.
        All commands arrive as DataChannel messages.
        """
        sid      = msg["session_id"]
        username = msg.get("from_user", "remote viewer")
        log.info(f"Incoming WebRTC offer from '{username}' — session {sid[:8]}")

        pc = RTCPeerConnection(RTCConfiguration(
            iceServers=[RTCIceServer(urls=s["urls"]) for s in STUN_SERVERS]
        ))
        active_pcs[sid] = pc

        @pc.on("datachannel")
        def on_datachannel(channel):
            log.info(f"DataChannel '{channel.label}' opened — P2P active for {sid[:8]}")
            # viewer_joined already emitted client_connected; just update status to confirm P2P
            self.status_changed.emit(f"P2P stream active: {username}")
            self._active_channels.append(channel)
            self._p2p_session_count += 1
            print(f"[SYNC] DataChannel opened — {self._p2p_session_count} P2P session(s); state_sync and frames now go direct")

            @channel.on("close")
            def on_dc_close():
                if channel in self._active_channels:
                    self._active_channels.remove(channel)
                self._p2p_session_count = max(0, self._p2p_session_count - 1)
                print(f"[SYNC] DataChannel closed — {self._p2p_session_count} P2P session(s) remaining")

            @channel.on("message")
            async def on_message(raw):
                try:
                    command = json.loads(raw)
                except Exception:
                    return
                result = await self._dispatch(command)
                try:
                    channel.send(json.dumps(result))
                except Exception as exc:
                    log.warning(f"DataChannel send failed: {exc}")

        @pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate:
                try:
                    await ws.send(json.dumps({
                        "type":       "ice",
                        "session_id": sid,
                        "candidate":  {
                            "candidate":     candidate.candidate,
                            "sdpMid":        candidate.sdpMid,
                            "sdpMLineIndex": candidate.sdpMLineIndex,
                        },
                    }))
                except Exception:
                    pass

        @pc.on("connectionstatechange")
        async def on_state():
            log.info(f"P2P [{sid[:8]}]: {pc.connectionState}")
            if pc.connectionState in ("failed", "closed"):
                active_pcs.pop(sid, None)
                self.client_disconnected.emit(username)
                self.status_changed.emit("Remote viewer disconnected — waiting for next")
                try:
                    await ws.send(json.dumps({"type": "end_session", "session_id": sid}))
                except Exception:
                    pass

        try:
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=msg["sdp"], type="offer"))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await ws.send(json.dumps({
                "type":       "answer",
                "session_id": sid,
                "sdp":        pc.localDescription.sdp,
            }))
            log.info(f"Answer sent for session {sid[:8]}")
        except Exception as exc:
            log.exception(f"WebRTC session setup failed for {sid[:8]}: {exc}")
            active_pcs.pop(sid, None)

    # ── Proxy fallback (relay-mediated commands, no WebRTC) ───────────────────

    async def _handle_proxy_fallback(self, ws, msg: dict) -> None:
        """
        Handle a proxy_command arriving via relay (WebRTC P2P was not established).
        Also activates frame pushing if the relay hasn't sent viewer_joined yet
        (backward-compatible with older relay versions).
        """
        if self._viewer_count == 0:
            self._viewer_count = 1
            log.info("proxy_command received — activating frame push (relay compat)")
            # Old relay didn't send viewer_joined — emit client_connected as fallback
            self.client_connected.emit("remote viewer")
        cmd_id = msg.get("cmd_id", "")
        result = await self._dispatch(msg)
        try:
            await ws.send(json.dumps({
                "type":   "proxy_response",
                "cmd_id": cmd_id,
                **result,
            }))
        except Exception as exc:
            log.warning(f"proxy_response send failed: {exc}")

    # ── Command dispatcher ────────────────────────────────────────────────────

    async def _dispatch(self, command: dict) -> dict:
        """
        Single dispatcher for all commands from remote clients.
        Called from DataChannel messages OR proxy_command fallback.

        Runs hardware calls in a thread executor so the asyncio event loop
        is never blocked by synchronous stage/camera operations.
        """
        action = command.get("action", "")
        params = command.get("params", {})
        cmd_id = command.get("cmd_id", "")

        # Keep relay alive even before hardware is ready; fail only HW-dependent actions.
        if not self._hw.is_ready() and action not in ("scan_status", "files_list", "file_download"):
            return {
                "status": "error",
                "message": "Hardware not ready",
                "cmd_id": cmd_id,
                "action": action,
            }

        loop   = asyncio.get_event_loop()

        try:
            # ── Read-only, fast ───────────────────────────────────────────
            if action == "capture":
                b64 = await loop.run_in_executor(None, self._hw.grab_jpeg_b64)
                result = {"status": "ok", "image": b64, "format": "jpeg"}

            elif action == "get_position":
                pos = await loop.run_in_executor(None, self._hw.get_position)
                result = {"status": "ok", "position": pos}

            elif action == "get_metadata":
                meta = await loop.run_in_executor(None, self._hw.get_metadata)
                result = {"status": "ok", "metadata": meta}

            # ── Stage control ─────────────────────────────────────────────
            elif action == "move":
                x = int(params.get("x", 0))
                y = int(params.get("y", 0))
                z = int(params.get("z", 0))
                pos = await loop.run_in_executor(
                    None, lambda: self._hw.move_relative(x=x, y=y, z=z))
                result = {"status": "ok", "position": pos}
                self.position_requested.emit(
                    float(pos.get("x", 0)),
                    float(pos.get("y", 0)),
                    float(pos.get("z", 0)),
                )
                # Broadcast position to all viewers immediately
                self.broadcast_state({
                    "kind": "position",
                    "x": float(pos.get("x", 0)),
                    "y": float(pos.get("y", 0)),
                    "z": float(pos.get("z", 0)),
                })

            elif action == "move_to":
                x = float(params["x"])
                y = float(params["y"])
                z = float(params["z"])
                try:
                    pos = await loop.run_in_executor(
                        None, lambda: self._hw.move_absolute(x=x, y=y, z=z))
                except Exception:
                    # Compatibility fallback for backends that require integer coordinates.
                    xi = int(round(x))
                    yi = int(round(y))
                    zi = int(round(z))
                    pos = await loop.run_in_executor(
                        None, lambda: self._hw.move_absolute(x=xi, y=yi, z=zi))
                result = {"status": "ok", "position": pos}
                self.position_requested.emit(
                    float(pos.get("x", 0)),
                    float(pos.get("y", 0)),
                    float(pos.get("z", 0)),
                )
                # Broadcast position to all viewers immediately
                self.broadcast_state({
                    "kind": "position",
                    "x": float(pos.get("x", 0)),
                    "y": float(pos.get("y", 0)),
                    "z": float(pos.get("z", 0)),
                })

            elif action == "set_step_size":
                step = float(params.get("value", 10.0))
                self.step_size_requested.emit(step)
                result = {"status": "ok", "step_size": step}
                self.broadcast_state({
                    "kind": "step_size",
                    "value": step,
                })

            elif action == "zero":
                pos = await loop.run_in_executor(None, self._hw.set_zero)
                result = {"status": "ok", "position": pos}
                self.position_requested.emit(
                    float(pos.get("x", 0)),
                    float(pos.get("y", 0)),
                    float(pos.get("z", 0)),
                )
                # Broadcast position to all viewers immediately
                self.broadcast_state({
                    "kind": "position",
                    "x": float(pos.get("x", 0)),
                    "y": float(pos.get("y", 0)),
                    "z": float(pos.get("z", 0)),
                })

            # ── Autofocus ─────────────────────────────────────────────────
            elif action == "autofocus":
                dz = int(params.get("dz", 10000))
                af_result = await loop.run_in_executor(
                    None, lambda: self._hw.run_autofocus(dz=dz))
                result = {"status": "ok", "autofocus_result": af_result}

            # ── Scan control ──────────────────────────────────────────────
            elif action == "scan_start":
                if self._hw.scan_manager is None:
                    result = {"status": "error", "message": "ScanManager not available"}
                else:
                    ok = self._hw.scan_manager.start(
                        cols      = int(params.get("cols", 3)),
                        rows      = int(params.get("rows", 3)),
                        overlap   = int(params.get("overlap", 10)),
                        pattern   = params.get("pattern", "raster"),
                        label     = params.get("label", ""),
                        objective = params.get("objective", "10x"),
                    )
                    result = {"status": "ok" if ok else "error",
                              "message": "started" if ok else "scan already running"}

            elif action == "scan_pause":
                if self._hw.scan_manager:
                    self._hw.scan_manager.pause()
                result = {"status": "ok"}

            elif action == "scan_resume":
                if self._hw.scan_manager:
                    self._hw.scan_manager.resume()
                result = {"status": "ok"}

            elif action == "scan_cancel":
                if self._hw.scan_manager:
                    self._hw.scan_manager.cancel()
                result = {"status": "ok"}

            elif action == "scan_status":
                if self._hw.scan_manager:
                    result = {"status": "ok",
                              "scan_status": self._hw.scan_manager.get_status()}
                else:
                    result = {"status": "ok", "scan_status": {"state": "IDLE"}}

            # ── UI sync ───────────────────────────────────────────────────
            elif action == "navigate":
                page = str(params.get("page", "")).strip()
                # Map client page names → server page names
                client_to_server = {"live": "manual", "calibration": "cal"}
                server_page = client_to_server.get(page, page)
                if server_page:
                    self.navigate_requested.emit(server_page)
                    result = {"status": "ok", "page": server_page}
                else:
                    result = {"status": "error", "message": "Missing page"}

            # ── Files ─────────────────────────────────────────────────────
            elif action == "files_list":
                files = await loop.run_in_executor(None, self._list_scans)
                result = {"status": "ok", "files": files}

            elif action == "file_download":
                name   = str(params.get("name", ""))
                offset = int(params.get("offset", 0))
                chunk  = int(params.get("chunk_size", 256 * 1024))  # 256 KB default
                data, total, done = await loop.run_in_executor(
                    None, lambda: self._read_file_chunk(name, offset, chunk)
                )
                result = {
                    "status": "ok",
                    "name":   name,
                    "offset": offset,
                    "total":  total,
                    "done":   done,
                    "data":   data,   # base64 chunk — only for file transfer, not live streaming
                }

            # ── Unknown ───────────────────────────────────────────────────
            else:
                result = {"status": "error", "message": f"Unknown action: {action}"}

        except Exception as exc:
            log.exception(f"Dispatch error for action '{action}': {exc}")
            result = {"status": "error", "message": str(exc)}

        result["cmd_id"] = cmd_id
        result["action"] = action
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _list_scans(self) -> list:
        import Vyuhaa_api
        scans_dir = Vyuhaa_api.SCANS_DIR
        if not os.path.exists(scans_dir):
            return []
        result = []
        for name in sorted(os.listdir(scans_dir), reverse=True):
            path = os.path.join(scans_dir, name)
            if os.path.isdir(path):
                tiles = [f for f in os.listdir(path) if f.lower().endswith((".jpg", ".jpeg", ".png", ".tiff", ".tif"))]
                total_bytes = 0
                for root, _dirs, files in os.walk(path):
                    for f in files:
                        try:
                            total_bytes += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                mtime = os.path.getmtime(path)
                file_type = "scan"
                if "cal" in name.lower() or "flatfield" in name.lower():
                    file_type = "cal"
                result.append({
                    "name":       name,
                    "type":       file_type,
                    "tiles":      len(tiles),
                    "size_bytes": total_bytes,
                    "modified":   mtime,
                    "path":       path,
                })
            else:
                try:
                    total_bytes = os.path.getsize(path)
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in (".zip", ".tar", ".gz"):
                    file_type = "zip"
                elif ext in (".ndpi", ".vsi"):
                    file_type = "wsi"
                elif ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                    file_type = "capture"
                elif ext in (".json", ".npy", ".csv") and ("cal" in name.lower() or "flat" in name.lower()):
                    file_type = "cal"
                else:
                    file_type = "capture"
                result.append({
                    "name":       name,
                    "type":       file_type,
                    "tiles":      1,
                    "size_bytes": total_bytes,
                    "modified":   mtime,
                    "path":       path,
                })
        return result

    def _read_file_chunk(self, name: str, offset: int, chunk_size: int):
        """
        Read *chunk_size* bytes at *offset* from the OME-TIFF / zip for the named scan.
        Returns (base64_data, total_bytes, is_done).
        Falls back to a zip of the scan folder when no single file exists.
        """
        import base64, zipfile, io, time
        import Vyuhaa_api
        scans_dir = Vyuhaa_api.SCANS_DIR
        scan_path = os.path.realpath(os.path.join(scans_dir, name))
        if not scan_path.startswith(os.path.realpath(scans_dir) + os.sep):
            return ("", 0, True)
        if not os.path.exists(scan_path):
            return ("", 0, True)

        # If the path is a regular file, serve it directly
        if os.path.isfile(scan_path):
            total = os.path.getsize(scan_path)
            with open(scan_path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(chunk_size)
            done = (offset + len(chunk)) >= total
            return (base64.b64encode(chunk).decode(), total, done)

        # Look for a pre-existing archive or OME-TIFF first
        for candidate in (name + ".zip", name + ".tiff", name + ".ome.tiff"):
            cpath = os.path.join(scans_dir, candidate)
            if os.path.exists(cpath):
                total = os.path.getsize(cpath)
                with open(cpath, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read(chunk_size)
                done = (offset + len(chunk)) >= total
                return (base64.b64encode(chunk).decode(), total, done)

        # No pre-built archive — stream a zip on-the-fly using BytesIO
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(scan_path):
                for f in sorted(files):
                    full = os.path.join(root, f)
                    arcname = os.path.relpath(full, scans_dir)
                    zf.write(full, arcname)
        total = buf.tell()
        buf.seek(offset)
        chunk = buf.read(chunk_size)
        done = (offset + len(chunk)) >= total
        return (base64.b64encode(chunk).decode(), total, done)

    async def _push_frames(self, ws) -> None:
        """
        Push JPEG frames at 20fps.
        - When P2P DataChannel is open: send binary JPEG directly (no relay).
        - When no P2P (proxy_command fallback): encode base64 and send via relay.
        """
        import cv2
        while self._active:
            if self._viewer_count > 0:
                try:
                    frame = self._hw.camera._last_display_frame if self._hw.camera else None
                    if frame is not None:
                        ok, buf = cv2.imencode(
                            ".jpg", frame,
                            [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY]
                        )
                        if ok:
                            jpeg_bytes = buf.tobytes()

                            if self._active_channels:
                                # ── P2P path: binary JPEG direct to DataChannel(s) ──
                                for ch in list(self._active_channels):
                                    try:
                                        ch.send(jpeg_bytes)
                                    except Exception:
                                        pass
                            else:
                                # ── Relay fallback: base64 JSON to relay WebSocket ──
                                transport = getattr(ws, "transport", None)
                                if transport is not None and transport.get_write_buffer_size() > 256 * 1024:
                                    await asyncio.sleep(0.01)
                                    continue
                                b64 = base64.b64encode(jpeg_bytes).decode()
                                await asyncio.wait_for(
                                    ws.send(json.dumps({"type": "stream_frame", "jpeg": b64})),
                                    timeout=0.03,
                                )
                except asyncio.TimeoutError:
                    # Skip this frame if socket is slow; avoid latency buildup.
                    pass
                except Exception:
                    pass
            await asyncio.sleep(FRAME_PUSH_INTERVAL)  # sleeps when no viewer

    async def _heartbeat(self, ws) -> None:
        """Send application-level ping every HEARTBEAT_INTERVAL seconds."""
        while self._active:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await ws.send(json.dumps({"type": "ping"}))
            except Exception:
                break

    async def _add_ice(self, pc: "RTCPeerConnection", msg: dict) -> None:
        c = msg.get("candidate", {})
        try:
            await pc.addIceCandidate(RTCIceCandidate(
                candidate     = c.get("candidate", ""),
                sdpMid        = c.get("sdpMid"),
                sdpMLineIndex = c.get("sdpMLineIndex"),
            ))
        except Exception as exc:
            log.warning(f"addIceCandidate error: {exc}")
