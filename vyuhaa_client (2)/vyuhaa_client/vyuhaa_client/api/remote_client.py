"""
remote_client.py — Python SDK for remote operators to control a microscope.

Usage
-----
  import asyncio
  from api.remote_client import RemoteMicroscopeClient

  async def main():
      client = RemoteMicroscopeClient(
          signaling_url = "wss://your-relay.com:8765",
          username      = "john",
          password      = "secret",
      )
      await client.login()

      # See which microscopes you can access
      for m in client.microscopes:
          print(m["microscope_id"], "online:", m["online"], "busy:", m["busy"])

      # Connect to a specific microscope via P2P
      scope = await client.connect_microscope("clinic-vyuhaa-001")

      pos   = await scope.get_position()
      image = await scope.capture()
      image.save("frame.png")

      await scope.move(x=100, y=0, z=0)
      await scope.autofocus()

      await scope.disconnect()
      await client.logout()

  asyncio.run(main())
"""

from __future__ import annotations
import asyncio
import base64
import io
import json
import logging
import os
import uuid
from typing import Any, Callable, Dict, List, Optional

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, RTCDataChannel
from PIL import Image

log = logging.getLogger("client")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

STUN_SERVERS = [
    {"urls": "stun:stun.l.google.com:19302"},
    {"urls": "stun:stun1.l.google.com:19302"},
    # Add TURN for hospital networks:
    # {"urls": "turn:your-turn.com:3478", "username": "u", "credential": "p"},
]


# ---------------------------------------------------------------------------
# Microscope proxy — what you get back from connect_microscope()
# ---------------------------------------------------------------------------

class MicroscopeProxy:
    """
    Represents a live P2P connection to a remote microscope.
    Once open, all commands flow directly between peers — the relay server
    is no longer on the data path.
    """

    def __init__(self, microscope_id: str, session_id: str):
        self.microscope_id = microscope_id
        self.session_id    = session_id
        self._channel: Optional[RTCDataChannel] = None
        self._pending: Dict[str, asyncio.Future] = {}  # cmd_id → Future
        self._connected = asyncio.Event()
        self._on_close: Optional[Callable] = None

    def _attach_channel(self, channel: RTCDataChannel):
        self._channel = channel
        channel.on("open",    lambda: self._connected.set())
        channel.on("message", self._on_message)
        channel.on("close",   self._on_channel_close)

    def _on_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        cmd_id = msg.get("cmd_id", "")
        future = self._pending.pop(cmd_id, None)
        if future and not future.done():
            future.set_result(msg)

    def _on_channel_close(self):
        log.info(f"P2P channel closed for {self.microscope_id}")
        # Fail all pending commands
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("DataChannel closed"))
        self._pending.clear()
        if self._on_close:
            self._on_close()

    async def _send(self, action: str, params: dict = {}, timeout: float = 30.0) -> dict:
        await asyncio.wait_for(self._connected.wait(), timeout=5.0)
        cmd_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[cmd_id] = future
        self._channel.send(json.dumps({"action": action, "params": params, "cmd_id": cmd_id}))
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise TimeoutError(f"Command '{action}' timed out after {timeout}s")
        return result

    # ── Public microscope API ───────────────────────────────────────────────

    async def capture(self) -> Image.Image:
        """Capture a frame and return a PIL Image."""
        resp = await self._send("capture", timeout=30.0)
        raw  = base64.b64decode(resp["image"])
        return Image.open(io.BytesIO(raw))

    async def get_position(self) -> dict:
        return (await self._send("get_position"))["position"]

    async def move(self, x: int = 0, y: int = 0, z: int = 0) -> dict:
        return await self._send("move", {"x": x, "y": y, "z": z})

    async def move_to(self, x: int, y: int, z: int) -> dict:
        return await self._send("move_to", {"x": x, "y": y, "z": z})

    async def autofocus(self) -> dict:
        return await self._send("autofocus", timeout=60.0)

    async def set_light(self, intensity: float) -> dict:
        return await self._send("set_light", {"intensity": intensity})

    async def get_metadata(self) -> dict:
        return (await self._send("get_metadata"))["metadata"]

    async def disconnect(self):
        if self._channel:
            self._channel.close()


# ---------------------------------------------------------------------------
# Main client class
# ---------------------------------------------------------------------------

class RemoteMicroscopeClient:

    def __init__(self, signaling_url: str, username: str, password: str):
        self.signaling_url  = signaling_url
        self.username       = username
        self.password       = password

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._access_token: str = ""
        self._refresh_token: str = ""
        self.microscopes: List[dict] = []

        # session_id → MicroscopeProxy
        self._proxies: Dict[str, MicroscopeProxy] = {}
        # microscope_id → RTCPeerConnection
        self._pcs: Dict[str, RTCPeerConnection] = {}

        self._recv_task:      Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    # ── Auth ────────────────────────────────────────────────────────────────

    async def login(self):
        self._ws = await websockets.connect(
            self.signaling_url,
            ping_interval = None,
            ping_timeout  = None,
            max_size      = 10 * 1024 * 1024,
            open_timeout  = 30,
        )
        await self._ws.send(json.dumps({
            "type":     "login",
            "username": self.username,
            "password": self.password,
        }))
        resp = json.loads(await self._ws.recv())
        if resp.get("type") == "error":
            raise PermissionError(f"Login failed: {resp['message']}")

        self._access_token  = resp["access_token"]
        self._refresh_token = resp["refresh_token"]
        self.microscopes    = resp["microscopes"]

        log.info(f"Logged in as '{self.username}'. "
                 f"{len(self.microscopes)} microscope(s) accessible.")

        # Start background receiver and heartbeat
        self._stop_event.clear()
        self._recv_task      = asyncio.create_task(self._receive_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def refresh_token(self):
        await self._ws.send(json.dumps({
            "type":          "refresh",
            "refresh_token": self._refresh_token,
        }))
        resp = json.loads(await self._ws.recv())
        if resp.get("type") == "error":
            raise PermissionError("Token refresh failed. Please log in again.")
        self._access_token  = resp["access_token"]
        self._refresh_token = resp["refresh_token"]
        log.info("Tokens refreshed.")

    async def logout(self):
        self._stop_event.set()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()

    # ── Connect to microscope ───────────────────────────────────────────────

    async def connect_microscope(
        self,
        microscope_id: str,
        timeout: float = 30.0,
    ) -> MicroscopeProxy:

        pc = RTCPeerConnection({"iceServers": STUN_SERVERS})
        self._pcs[microscope_id] = pc

        channel = pc.createDataChannel("microscope-control", ordered=True)

        proxy = MicroscopeProxy(microscope_id, "")
        proxy._attach_channel(channel)

        offer_sent     = asyncio.Event()
        session_id_box = [None]  # mutable box

        @pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate and session_id_box[0]:
                await self._ws.send(json.dumps({
                    "type":       "ice",
                    "session_id": session_id_box[0],
                    "access_token": self._access_token,
                    "candidate": {
                        "candidate":     candidate.candidate,
                        "sdpMid":        candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    },
                }))

        # Create and send offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        await self._ws.send(json.dumps({
            "type":          "offer",
            "access_token":  self._access_token,
            "microscope_id": microscope_id,
            "sdp":           pc.localDescription.sdp,
        }))

        # Wait for answer from receiver loop
        answer_event = asyncio.Event()
        self._pending_answers = getattr(self, "_pending_answers", {})
        self._pending_answers[microscope_id] = (pc, proxy, session_id_box, answer_event)

        await asyncio.wait_for(answer_event.wait(), timeout=timeout)
        await asyncio.wait_for(proxy._connected.wait(), timeout=timeout)

        self._proxies[proxy.session_id] = proxy
        log.info(f"P2P connected to '{microscope_id}' (session {proxy.session_id[:8]})")
        return proxy

    # ── Background receiver ─────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        heartbeat_interval = int(os.environ.get("HEARTBEAT_INTERVAL", "10"))
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(),
                                       timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            try:
                if self._ws and not self._ws.closed:
                    await self._ws.send(json.dumps({"type": "ping"}))
                    log.debug("Heartbeat ping sent")
            except Exception:
                break

    async def _receive_loop(self):
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                await self._dispatch(msg)
        except websockets.exceptions.ConnectionClosed:
            log.warning("Signaling connection closed.")
        except asyncio.CancelledError:
            pass

    async def _dispatch(self, msg: dict):
        msg_type = msg.get("type", "")

        if msg_type == "pong":
            return

        elif msg_type == "error":
            log.error(f"Server error [{msg.get('code')}]: {msg.get('message')}")
            for mid, (pc, proxy, _, ev) in list(getattr(self, "_pending_answers", {}).items()):
                ev.set()

        elif msg_type == "answer":
            sdp        = msg["sdp"]
            session_id = msg["session_id"]
            for mid, (pc, proxy, session_id_box, ev) in list(
                getattr(self, "_pending_answers", {}).items()
            ):
                if not ev.is_set():
                    session_id_box[0]   = session_id
                    proxy.session_id    = session_id
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=sdp, type="answer")
                    )
                    ev.set()
                    del self._pending_answers[mid]
                    break

        elif msg_type == "ice":
            session_id = msg.get("session_id", "")
            proxy = self._proxies.get(session_id)
            if proxy:
                for mid, pc in self._pcs.items():
                    raw_c = msg.get("candidate", {})
                    candidate = RTCIceCandidate(
                        candidate     = raw_c.get("candidate", ""),
                        sdpMid        = raw_c.get("sdpMid"),
                        sdpMLineIndex = raw_c.get("sdpMLineIndex"),
                    )
                    await pc.addIceCandidate(candidate)
                    break

        elif msg_type == "session_ended":
            session_id = msg.get("session_id", "")
            reason     = msg.get("reason", "unknown")
            proxy = self._proxies.pop(session_id, None)
            if proxy:
                log.info(f"Session {session_id[:8]} ended: {reason}")

        else:
            log.debug(f"Unhandled: {msg_type}")


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    async def demo():
        client = RemoteMicroscopeClient(
            signaling_url = os.environ.get("SIGNALING_URL", "wss://your-relay.com:8765"),
            username      = os.environ.get("MS_USERNAME", "john"),
            password      = os.environ.get("MS_PASSWORD", "secret"),
        )

        print("Logging in...")
        await client.login()

        print("\nAccessible microscopes:")
        for m in client.microscopes:
            status = "🟢 online" if m["online"] else "🔴 offline"
            busy   = " [BUSY]"  if m["busy"]   else ""
            print(f"  {m['microscope_id']} — {m['display_name']} {status}{busy}")

        target = sys.argv[1] if len(sys.argv) > 1 else client.microscopes[0]["microscope_id"]
        print(f"\nConnecting to '{target}'...")
        scope = await client.connect_microscope(target)

        pos = await scope.get_position()
        print(f"Position: {pos}")

        print("Capturing image...")
        image = await scope.capture()
        image.save("remote_capture.png")
        print("Saved: remote_capture.png")

        await scope.disconnect()
        await client.logout()

    asyncio.run(demo())
