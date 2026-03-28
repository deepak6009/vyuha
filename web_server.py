"""
web_server.py
─────────────
Standalone FastAPI web server for Vyuhaa Microscope in simulation mode.

Runs without PySide6 or hardware — uses simulated camera + dummy stage.
Serves on 0.0.0.0:5000 for the Replit preview.

Usage:
    python web_server.py
"""

from __future__ import annotations

import sys
import os
import io
import asyncio
import logging
import json
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("web_server")

MICROSCOPE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vyuhaa_microscope_v2.1.0_updated (2)",
    "vyuhaa_microscope_v2.1.0_updated",
)
sys.path.insert(0, MICROSCOPE_DIR)

os.environ.setdefault("VYUHAA_SIMULATION", "1")
os.environ.setdefault("HARDWARE_TYPE", "simulation")

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from vyuhaa.things.stage.dummy import DummyStage
from vyuhaa.things.camera.simulation import SimulatedCamera

log.info("Initialising simulation hardware...")

_stage = DummyStage()
_stage.__enter__()

_camera = SimulatedCamera(thing_server_interface=None)
_camera._stage = _stage
_camera.__enter__()

log.info("Simulation hardware ready.")

app = FastAPI(title="Vyuhaa Microscope API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _jpeg_frame() -> bytes:
    """Grab the latest JPEG frame from the simulated camera."""
    import numpy as np
    import cv2
    arr = _camera.capture_array()
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    if arr.ndim == 3 and arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


@app.get("/", response_class=HTMLResponse)
async def index():
    """Simple browser UI for the Vyuhaa Microscope simulation."""
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Vyuhaa Microscope — Simulation</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background: #1a1a2e;
      color: #e0e0e0;
      min-height: 100vh;
    }
    header {
      background: #16213e;
      padding: 12px 24px;
      display: flex;
      align-items: center;
      gap: 16px;
      border-bottom: 2px solid #c5247f;
    }
    header h1 { font-size: 1.4rem; color: #c5247f; }
    header .subtitle { font-size: 0.85rem; color: #888; }
    .badge {
      background: #c5247f22;
      border: 1px solid #c5247f;
      border-radius: 12px;
      padding: 2px 10px;
      font-size: 0.75rem;
      color: #c5247f;
      margin-left: auto;
    }
    main {
      display: grid;
      grid-template-columns: 1fr 320px;
      gap: 16px;
      padding: 16px;
      max-width: 1200px;
      margin: 0 auto;
    }
    .camera-panel {
      background: #16213e;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid #0f3460;
    }
    .camera-panel h2 {
      padding: 12px 16px;
      font-size: 0.9rem;
      border-bottom: 1px solid #0f3460;
      color: #aaa;
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    #camera-img {
      width: 100%;
      display: block;
      background: #000;
    }
    .controls {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .panel {
      background: #16213e;
      border-radius: 8px;
      border: 1px solid #0f3460;
      padding: 16px;
    }
    .panel h2 {
      font-size: 0.85rem;
      color: #aaa;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 12px;
    }
    .pos-display {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 8px;
      margin-bottom: 12px;
    }
    .pos-item {
      background: #0f3460;
      border-radius: 6px;
      padding: 8px;
      text-align: center;
    }
    .pos-item label { font-size: 0.7rem; color: #888; display: block; }
    .pos-item .val { font-size: 1.1rem; font-weight: 600; color: #e0e0e0; }
    .move-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
    }
    .move-grid button {
      background: #0f3460;
      color: #e0e0e0;
      border: 1px solid #1a4080;
      border-radius: 6px;
      padding: 10px;
      cursor: pointer;
      font-size: 0.9rem;
      transition: background 0.2s;
    }
    .move-grid button:hover { background: #1a4080; }
    .move-grid button:disabled { opacity: 0.4; cursor: default; }
    .move-grid .placeholder { visibility: hidden; }
    .step-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 10px;
    }
    .step-row label { font-size: 0.8rem; color: #888; white-space: nowrap; }
    .step-row input {
      flex: 1;
      background: #0f3460;
      border: 1px solid #1a4080;
      border-radius: 4px;
      color: #e0e0e0;
      padding: 4px 8px;
      font-size: 0.85rem;
    }
    .z-row {
      display: flex;
      gap: 6px;
      margin-top: 8px;
    }
    .z-row button {
      flex: 1;
      background: #0f3460;
      color: #e0e0e0;
      border: 1px solid #1a4080;
      border-radius: 6px;
      padding: 8px;
      cursor: pointer;
      font-size: 0.85rem;
    }
    .z-row button:hover { background: #1a4080; }
    .action-btn {
      width: 100%;
      background: #c5247f;
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 10px;
      cursor: pointer;
      font-size: 0.9rem;
      font-weight: 600;
      margin-top: 8px;
      transition: background 0.2s;
    }
    .action-btn:hover { background: #a01e6a; }
    .action-btn:disabled { opacity: 0.5; cursor: default; }
    .status {
      font-size: 0.8rem;
      color: #888;
      margin-top: 8px;
      min-height: 18px;
    }
    .api-info {
      font-size: 0.75rem;
      color: #666;
      padding: 8px;
      background: #0d1b2a;
      border-radius: 4px;
      font-family: monospace;
      word-break: break-all;
    }
    .api-info a { color: #4da6ff; text-decoration: none; }
    .api-info a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Vyuhaa Microscope</h1>
      <div class="subtitle">Microscope Control &amp; Remote Sharing</div>
    </div>
    <div class="badge">Simulation Mode</div>
  </header>

  <main>
    <div class="camera-panel">
      <h2>Live Camera Feed</h2>
      <img id="camera-img" src="/api/v2/streams/snapshot" alt="Camera feed"/>
    </div>

    <div class="controls">
      <div class="panel">
        <h2>Stage Position</h2>
        <div class="pos-display">
          <div class="pos-item"><label>X</label><div class="val" id="pos-x">0</div></div>
          <div class="pos-item"><label>Y</label><div class="val" id="pos-y">0</div></div>
          <div class="pos-item"><label>Z</label><div class="val" id="pos-z">0</div></div>
        </div>

        <div class="move-grid">
          <div class="placeholder"></div>
          <button onclick="move(0, step())">▲</button>
          <div class="placeholder"></div>
          <button onclick="move(-step(), 0)">◀</button>
          <div class="placeholder"></div>
          <button onclick="move(step(), 0)">▶</button>
          <div class="placeholder"></div>
          <button onclick="move(0, -step())">▼</button>
          <div class="placeholder"></div>
        </div>

        <div class="z-row">
          <button onclick="moveZ(step())">Z ▲</button>
          <button onclick="moveZ(-step())">Z ▼</button>
        </div>

        <div class="step-row">
          <label>Step (µm):</label>
          <input type="number" id="step-size" value="500" min="1" max="50000"/>
        </div>

        <button class="action-btn" onclick="zeroStage()">Zero Position</button>
        <div class="status" id="stage-status"></div>
      </div>

      <div class="panel">
        <h2>Camera</h2>
        <button class="action-btn" onclick="captureSnapshot()">Capture Snapshot</button>
        <div class="status" id="camera-status"></div>
      </div>

      <div class="panel">
        <h2>API Docs</h2>
        <div class="api-info">
          <a href="/docs" target="_blank">/docs — Swagger UI</a><br/>
          <a href="/api/v2/instrument" target="_blank">/api/v2/instrument</a><br/>
          <a href="/api/v2/instrument/position" target="_blank">/api/v2/instrument/position</a><br/>
          <a href="/api/v2/streams/snapshot" target="_blank">/api/v2/streams/snapshot</a>
        </div>
      </div>
    </div>
  </main>

  <script>
    function step() { return parseInt(document.getElementById('step-size').value) || 500; }

    async function refreshPosition() {
      try {
        const r = await fetch('/api/v2/instrument/position');
        if (r.ok) {
          const p = await r.json();
          document.getElementById('pos-x').textContent = p.x ?? 0;
          document.getElementById('pos-y').textContent = p.y ?? 0;
          document.getElementById('pos-z').textContent = p.z ?? 0;
        }
      } catch(e) {}
    }

    async function move(x, y) {
      document.getElementById('stage-status').textContent = 'Moving...';
      try {
        const r = await fetch('/api/v2/instrument/actions/move', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({x, y, z: 0})
        });
        const d = await r.json();
        document.getElementById('stage-status').textContent = 'Done.';
        await refreshPosition();
        refreshCamera();
      } catch(e) {
        document.getElementById('stage-status').textContent = 'Error: ' + e;
      }
    }

    async function moveZ(z) {
      document.getElementById('stage-status').textContent = 'Moving Z...';
      try {
        const r = await fetch('/api/v2/instrument/actions/move', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({x: 0, y: 0, z})
        });
        const d = await r.json();
        document.getElementById('stage-status').textContent = 'Done.';
        await refreshPosition();
        refreshCamera();
      } catch(e) {
        document.getElementById('stage-status').textContent = 'Error: ' + e;
      }
    }

    async function zeroStage() {
      document.getElementById('stage-status').textContent = 'Zeroing...';
      try {
        const r = await fetch('/api/v2/instrument/actions/zero', {method:'POST'});
        document.getElementById('stage-status').textContent = 'Zeroed.';
        await refreshPosition();
      } catch(e) {
        document.getElementById('stage-status').textContent = 'Error: ' + e;
      }
    }

    function refreshCamera() {
      const img = document.getElementById('camera-img');
      img.src = '/api/v2/streams/snapshot?t=' + Date.now();
    }

    async function captureSnapshot() {
      document.getElementById('camera-status').textContent = 'Capturing...';
      refreshCamera();
      document.getElementById('camera-status').textContent = 'Snapshot refreshed.';
      setTimeout(() => document.getElementById('camera-status').textContent = '', 2000);
    }

    // Auto-refresh camera every 500ms
    setInterval(refreshCamera, 500);
    // Refresh position every second
    setInterval(refreshPosition, 1000);
    refreshPosition();
  </script>
</body>
</html>
""")


@app.get("/api/v2/streams/snapshot")
@app.head("/api/v2/streams/snapshot")
async def snapshot():
    """Return a JPEG snapshot from the simulated camera."""
    data = await asyncio.get_event_loop().run_in_executor(None, _jpeg_frame)
    return Response(content=data, media_type="image/jpeg")


@app.get("/api/v2/instrument/position")
async def get_position():
    """Get current stage position."""
    pos = dict(_stage.position)
    return JSONResponse(pos)


@app.get("/api/v2/instrument")
async def get_metadata():
    """Get microscope metadata."""
    import socket
    return {
        "hostname": socket.gethostname(),
        "make": "Vyuhaa",
        "model": "Vyuhaa LBC Microscope",
        "mode": "simulation",
        "position": dict(_stage.position),
        "camera_streaming": _camera.stream_active,
    }


@app.post("/api/v2/instrument/actions/move")
async def move(body: dict):
    """Relative stage move."""
    x = int(body.get("x", 0))
    y = int(body.get("y", 0))
    z = int(body.get("z", 0))
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: _stage._hardware_move_relative(x=x, y=y, z=z)
    )
    _stage.instantaneous_position = _stage._hardware_position
    return {"status": "ok", "position": dict(_stage.position)}


@app.post("/api/v2/instrument/actions/zero")
async def zero_stage():
    """Zero stage coordinates at current position."""
    _stage.set_zero_position()
    return {"status": "ok", "position": dict(_stage.position)}


@app.get("/routes")
def routes_stub():
    """Stub route list for Vyuhaa Connect discoverability."""
    urls = [
        "/api/v2/",
        "/api/v2/streams/snapshot",
        "/api/v2/instrument/settings/name",
    ]
    return {url: {"url": url, "methods": ["GET"]} for url in urls}


@app.get("/api/v2/instrument/settings/name")
def get_hostname():
    """Get hostname for Vyuhaa Connect compatibility."""
    import socket
    return socket.gethostname()


if __name__ == "__main__":
    log.info("Starting Vyuhaa Microscope web server on 0.0.0.0:5000")
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")
