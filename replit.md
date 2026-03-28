# Vyuhaa Microscope

## Overview

**Vyuhaa** is a microscope control and remote sharing system originally designed for NVIDIA Jetson hardware (Jetson Nano/Orin). It provides automated microscopy, a local touchscreen UI (PySide6), and remote-access via WebRTC/WebSocket.

In this Replit environment, it runs in **simulation mode** via a standalone FastAPI web server — no physical hardware required.

## Architecture

### Project Layout

```
web_server.py                          # ← Replit entry point (FastAPI, simulation mode)
vyuhaa_microscope_v2.1.0_updated (2)/
  vyuhaa_microscope_v2.1.0_updated/
    main.py                            # PySide6 desktop app (Jetson-only)
    Vyuhaa_api.py                      # Hardware abstraction layer
    hardware_registry.py               # Shared hardware singleton
    remote_sharing.py                  # WebRTC/WebSocket relay
    scan_manager.py                    # Whole-slide imaging scan manager
    requirements.txt                   # Python dependencies
    .env.example                       # Environment variable template
    vyuhaa/
      things/
        camera/
          simulation.py               # SimulatedCamera (blob-generating)
          opencv.py                    # OpenCV camera
          jetson.py                    # Jetson MIPI camera
        stage/
          dummy.py                     # DummyStage (no hardware)
          nema.py                      # NEMA stepper motor
          sangaboard.py                # Sangaboard controller
      stitching_engine/                # WSI tile stitching (Dask/OME-TIFF)
    server/
      __init__.py                      # FastAPI web server (ENABLE_WEB_UI=1)
      legacy_api.py                    # OF Connect compatibility endpoints
    pages/                             # PySide6 UI pages
vyuhaa_client (2)/
  vyuhaa_client/                       # Remote client desktop app (PySide6)
relay/                                 # WebSocket relay server
```

## Running the App

The Replit workflow runs:
```bash
python web_server.py
```

This starts a FastAPI server on `0.0.0.0:5000` with:
- Simulated camera (moving blob images)
- Dummy stage (software-only XYZ positioning)
- REST API for instrument control
- Live camera snapshot endpoint
- Simple browser UI

## Key Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Browser UI |
| `GET /docs` | Swagger API documentation |
| `GET /api/v2/streams/snapshot` | Current camera frame (JPEG) |
| `GET /api/v2/instrument` | Microscope metadata |
| `GET /api/v2/instrument/position` | Stage position {x, y, z} |
| `POST /api/v2/instrument/actions/move` | Relative stage move |
| `POST /api/v2/instrument/actions/zero` | Zero stage position |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VYUHAA_SIMULATION` | `1` | Force simulation mode |
| `HARDWARE_TYPE` | `simulation` | Hardware type override |
| `RELAY_URL` | — | WebSocket relay URL for remote sharing |
| `MICROSCOPE_ID` | — | Microscope identifier for relay |
| `API_KEY` | — | API key for relay authentication |
| `ENABLE_WEB_UI` | `0` | Enable web UI alongside PySide6 |
| `WEB_UI_PORT` | `5000` | Web UI port |

## Dependencies

Installed via pip:
- `fastapi`, `uvicorn` — Web server
- `opencv-python-headless` — Image processing (no display)
- `numpy`, `Pillow` — Numerical/image operations
- `pydantic` — Data validation
- `piexif` — JPEG EXIF metadata
- `requests`, `websockets` — HTTP/WebSocket clients

Not installed (Jetson-only):
- `PySide6` — Qt UI (requires display)
- `Jetson.GPIO` — GPIO control
- `aiortc` — WebRTC

## Notes

- The desktop app (`main.py`) requires PySide6 and a display — not runnable in Replit
- Simulation mode fully replaces hardware: camera generates synthetic cell-like images, stage moves are tracked in software
- The relay/remote-sharing features require an external relay server with valid credentials
