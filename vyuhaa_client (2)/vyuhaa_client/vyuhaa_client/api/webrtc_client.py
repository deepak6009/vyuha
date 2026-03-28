"""
webrtc_client.py — WebRTC P2P peer for Vyuhaa Remote Client.

Manages a single RTCPeerConnection in a background asyncio thread so it
doesn't block the Qt event loop.

Flow:
  1. create_offer()          → offer_ready(sdp) signal
  2. Caller sends SDP to relay via QWebSocket
  3. Relay returns answer → set_remote_description(sdp, "answer")
  4. ICE candidates exchanged via relay → add_ice_candidate(...)
  5. DataChannel "vyuhaa" opens → connected() signal
  6. Frames arrive as binary DataChannel messages → frame_received(bytes)
  7. Commands sent via send_command(action, params) → DataChannel text message
  8. Command responses arrive as text DataChannel messages → command_response(dict)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Optional

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("webrtc_client")

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
    _AIORTC_OK = True
except ImportError:
    _AIORTC_OK = False
    log.info("aiortc not installed — WebRTC P2P unavailable, relay proxy will be used")


class WebRTCPeer(QObject):
    """
    Thin asyncio wrapper around an aiortc RTCPeerConnection.

    All signals are safe to connect to Qt slots in any thread.
    """

    # Emitted when SDP offer is ready to be sent to relay
    offer_ready = Signal(str)
    # Emitted when an ICE candidate is ready to be sent to relay
    ice_ready = Signal(dict)
    # Raw JPEG bytes received directly from the microscope (no relay)
    frame_received = Signal(bytes)
    # Dict response to a command sent via DataChannel
    command_response = Signal(dict)
    # DataChannel is open — P2P is fully active
    connected = Signal()
    # DataChannel closed or connection failed
    disconnected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._pc: Optional["RTCPeerConnection"] = None
        self._channel = None
        self._running = False

    # ── Public API (thread-safe, callable from Qt thread) ─────────────────────

    @property
    def available(self) -> bool:
        """True if aiortc is installed."""
        return _AIORTC_OK

    @property
    def p2p_active(self) -> bool:
        """True when the DataChannel is open and ready for traffic."""
        return (
            self._channel is not None
            and getattr(self._channel, "readyState", "") == "open"
        )

    def start(self) -> None:
        """Start the background asyncio thread. Idempotent."""
        if self._running or not _AIORTC_OK:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="webrtc-peer"
        )
        self._thread.start()

    def create_offer(self) -> None:
        """Initiate WebRTC handshake. Triggers offer_ready signal when ready."""
        if not self._running or not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._create_offer(), self._loop)

    def set_remote_description(self, sdp: str, sdp_type: str) -> None:
        """Pass the SDP answer received from relay into the peer connection."""
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(
                self._set_remote_description(sdp, sdp_type), self._loop
            )

    def add_ice_candidate(self, candidate: dict) -> None:
        """Pass an ICE candidate received from relay into the peer connection."""
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(
                self._add_ice_candidate(candidate), self._loop
            )

    def send_command(self, action: str, params: dict | None = None,
                     cmd_id: str | None = None) -> str:
        """Send a hardware command via DataChannel (P2P, no relay).

        Returns the cmd_id used, or "" if channel is not open.
        """
        if not self.p2p_active:
            return ""
        cid = cmd_id or uuid.uuid4().hex[:8]
        msg = json.dumps({"action": action, "params": params or {}, "cmd_id": cid})
        self._loop.call_soon_threadsafe(self._channel.send, msg)
        return cid

    def stop(self) -> None:
        """Close the peer connection and stop the asyncio thread."""
        self._running = False
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(self._pc.close(), self._loop)
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._pc = None
        self._channel = None

    # ── Background asyncio thread ──────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _create_offer(self) -> None:
        self._pc = RTCPeerConnection()

        # Single DataChannel carries both commands (text) and frames (binary)
        self._channel = self._pc.createDataChannel("vyuhaa", ordered=True)

        @self._channel.on("open")
        def on_open():
            log.info("WebRTC DataChannel open — P2P active, relay bypassed for frames")
            self.connected.emit()

        @self._channel.on("close")
        def on_close():
            log.info("WebRTC DataChannel closed")
            self.disconnected.emit()

        @self._channel.on("message")
        def on_message(msg):
            if isinstance(msg, bytes):
                # Raw JPEG frame pushed directly by the microscope
                self.frame_received.emit(msg)
            else:
                try:
                    self.command_response.emit(json.loads(msg))
                except Exception:
                    pass

        @self._pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate:
                self.ice_ready.emit({
                    "candidate":     candidate.candidate,
                    "sdpMid":        candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                })

        @self._pc.on("connectionstatechange")
        async def on_state():
            state = self._pc.connectionState
            log.info(f"WebRTC connection state: {state}")
            if state in ("failed", "closed", "disconnected"):
                self.disconnected.emit()

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        self.offer_ready.emit(self._pc.localDescription.sdp)
        log.info("WebRTC offer created — waiting for answer from relay")

    async def _set_remote_description(self, sdp: str, sdp_type: str) -> None:
        try:
            await self._pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp, type=sdp_type)
            )
            log.info(f"WebRTC remote description set ({sdp_type})")
        except Exception as exc:
            log.warning(f"setRemoteDescription failed: {exc}")

    async def _add_ice_candidate(self, candidate: dict) -> None:
        try:
            await self._pc.addIceCandidate(RTCIceCandidate(
                candidate=candidate.get("candidate", ""),
                sdpMid=candidate.get("sdpMid"),
                sdpMLineIndex=candidate.get("sdpMLineIndex"),
            ))
        except Exception as exc:
            log.warning(f"addIceCandidate failed: {exc}")
