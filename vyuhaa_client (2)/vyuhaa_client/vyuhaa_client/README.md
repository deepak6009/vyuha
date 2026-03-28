# Vyuhaa Remote Client

PySide6 desktop application for remote microscope control via WebSocket relay.

## Structure

```
vyuhaa_client/
├── main.py                 # Entry point — MainWindow, keyboard shortcuts
├── styles.py               # QSS stylesheet + colour tokens
├── requirements.txt
├── api/
│   ├── app_state.py        # AppState singleton (all signals + shared data)
│   ├── api_client.py       # RelayClient — connection, stage RPC, scan sequencing
│   ├── connection.py       # Low-level WebSocket connection manager
│   └── remote_client.py    # Async Python SDK for programmatic access (WebRTC)
├── pages/
│   ├── home_page.py        # 3×2 tile navigation grid
│   ├── connect_page.py     # Topology diagram + relay/device config
│   ├── live_page.py        # Camera feed + stage D-pad + operator chat
│   ├── scan_page.py        # WSI scan config + tile-grid progress
│   ├── files_page.py       # File browser (grid/list, filter, export)
│   └── settings_page.py    # Tabbed settings (connection, streaming, stage, shortcuts, about)
└── widgets/
    ├── topbar.py           # Status pills (relay, device, latency)
    └── bottombar.py        # Context hints + Home button
```

## Setup

```bash
pip install -r requirements.txt
python main.py
```

## Keyboard Shortcuts (Live View)

| Key       | Action         |
|-----------|----------------|
| W / S     | Move stage Y   |
| A / D     | Move stage X   |
| + / −     | Focus Z        |
| Space     | Capture frame  |

## Architecture

- **AppState** — centralised QObject singleton; all pages read/write state here
- **RelayClient** — manages WebSocket relay connection + scan sequencing
- **ConnectionManager** — low-level WS + JSON-RPC endpoint wrappers
- **RemoteMicroscopeClient** — async WebRTC SDK for external Python scripts

When the relay server is unreachable, `RelayClient` automatically falls back
to a simulated connection so the UI can be exercised without hardware.
