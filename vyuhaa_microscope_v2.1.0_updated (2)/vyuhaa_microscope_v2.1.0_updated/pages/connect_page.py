"""
pages/connect_page.py
──────────────────────
Hardware discovery + relay broadcast setup.

Vyuhaa API hooks are clearly marked with # [VYUHAA API].
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QLineEdit, QFrame, QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QFont
import theme
import os
import Vyuhaa_api as ofa   # placeholder — see vyuhaa_api.py


class DiscoveryWorker(QThread):
    """Background thread to discover and connect so UI doesn't freeze."""
    discovery_event = Signal(list)
    connection_result = Signal(bool, object) # success, device
    error_occurred = Signal(str)

    def run(self):
        try:
            # 1. Discover
            devices = ofa.discover_devices()
            self.discovery_event.emit(devices)
            
            if devices:
                # 2. Connect (This is the blocking part: probes USB/GPIO/Cameras)
                device = devices[0]
                success = ofa.connect(device)
                self.connection_result.emit(success, device)
        except Exception as e:
            self.error_occurred.emit(str(e))


class LayerCard(QFrame):
    """A rounded card showing one connection layer (Hardware / Relay)."""

    def __init__(self, number: str, title: str, badge_text: str,
                 number_style: str = "hw", parent=None):
        super().__init__(parent)
        self.setObjectName("layerCard")
        self._build(number, title, badge_text, number_style)
        self._set_style(active=False)

    def _build(self, number, title, badge_text, number_style):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(6, 6, 6, 6)
        self.main_layout.setSpacing(0)

        # Header row
        header = QHBoxLayout()
        num_colours = {
            "hw":    ("rgba(34,197,94,0.15)",  theme.GREEN, "rgba(34,197,94,0.3)"),
            "relay": ("rgba(30,184,168,0.10)", theme.TEAL,  "rgba(30,184,168,0.3)"),
        }
        bg, fg, border = num_colours.get(number_style, num_colours["hw"])
        num_lbl = QLabel(number)
        num_lbl.setFixedSize(14, 14)
        num_lbl.setAlignment(Qt.AlignCenter)
        num_lbl.setStyleSheet(f"""
            QLabel {{
                background: {bg}; color: {fg};
                border: 1px solid {border}; border-radius: 7px;
                font-family: 'JetBrains Mono', monospace;
                font-size: 7px; font-weight: 700;
            }}
        """)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size:9px;font-weight:700;color:{theme.TEXT};background:transparent;")

        self.status_lbl = QLabel(badge_text)
        self.status_lbl.setObjectName("statusBadge")
        self._update_status_style("disconnected")

        header.addWidget(num_lbl)
        header.addWidget(title_lbl, stretch=1)
        header.addWidget(self.status_lbl, alignment=Qt.AlignRight)
        self.main_layout.addLayout(header)

        # Detail area (subclasses add widgets here via detail_layout)
        self.detail_frame = QFrame()
        self.detail_frame.setStyleSheet("background:transparent;")
        self.detail_layout = QVBoxLayout(self.detail_frame)
        self.detail_layout.setContentsMargins(0, 6, 0, 0)
        self.detail_layout.setSpacing(4)
        self.main_layout.addWidget(self.detail_frame)

    def set_status(self, text: str, state: str):
        """state: 'disconnected' | 'connected' | 'active' | 'idle'"""
        self.status_lbl.setText(text)
        self._update_status_style(state)

    def _update_status_style(self, state: str):
        styles = {
            "disconnected": (f"background:rgba(239,68,68,0.12);color:{theme.RED};border:1px solid rgba(239,68,68,0.3);"),
            "connected":    (f"background:rgba(34,197,94,0.12);color:{theme.GREEN};border:1px solid rgba(34,197,94,0.3);"),
            "active":       (f"background:rgba(30,184,168,0.10);color:{theme.TEAL};border:1px solid rgba(30,184,168,0.3);"),
            "idle":         (f"background:{theme.SURFACE};color:{theme.TEXT_DIM};border:1px solid {theme.BORDER};"),
        }
        s = styles.get(state, styles["idle"])
        self.status_lbl.setStyleSheet(f"""
            QLabel#statusBadge {{
                font-family:'JetBrains Mono',monospace;font-size:7px;
                font-weight:500;letter-spacing:0.04em;
                padding:1px 3px;border-radius:5px; {s}
            }}
        """)

    def _set_style(self, active: bool):
        border = "rgba(30,184,168,0.45)" if active else theme.BORDER
        shadow = "0 0 16px rgba(30,184,168,0.07);" if active else ""
        self.setStyleSheet(f"""
            QFrame#layerCard {{
                background:{theme.SURFACE2};
                border:1px solid {border};
                border-radius:10px;
            }}
        """)

    def set_active(self, active: bool):
        self._set_style(active)


# ──────────────────────────────────────────────────────────────────────────────

class ConnectPage(QWidget):
    """
    Two-panel connect screen.
    Signals:
        hw_connected(DeviceInfo)
        hw_disconnected()
        relay_connected(room: str)
        relay_disconnected()
    """

    hw_connected    = Signal(object)   # ofa.DeviceInfo
    hw_disconnected = Signal()
    relay_connected = Signal(str)
    relay_disconnected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hw_connected    = False
        self._relay_connected = False
        self._detect_timer    = QTimer(self)
        self._relay_timer     = QTimer(self)
        self._detect_timer.setSingleShot(True)
        self._relay_timer.setSingleShot(True)
        self._detect_timer.timeout.connect(self._on_hw_found)
        self._relay_timer.timeout.connect(self._on_relay_connected)
        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main_panel(), stretch=1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(125) # Reduced from 135
        sidebar.setStyleSheet(f"QFrame{{background:{theme.SURFACE};border-right:1px solid {theme.BORDER};}}")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Scrollable section
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(6, 12, 6, 8)
        scroll_layout.setSpacing(8)

        title = QLabel("Connect")
        title.setStyleSheet(f"""
            font-family:'Syne',sans-serif;font-size:11px;font-weight:700;
            color:{theme.WHITE};padding-bottom:8px;
            border-bottom:1px solid {theme.BORDER};
        """)
        sub = QLabel("Hardware & Relay Setup")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size:9px;color:{theme.TEXT_DIM};line-height:1.4;margin-bottom:4px;")

        scroll_layout.addWidget(title)
        scroll_layout.addWidget(sub)

        # Hardware layer card
        self.hw_card = LayerCard("1", "Hardware", "SEARCHING", "hw")
        self.hw_detail_lbl = QLabel("No device found\nChecking local environment for Vyuhaa modules…")
        self.hw_detail_lbl.setWordWrap(True)
        self.hw_detail_lbl.setStyleSheet(f"""
            font-size:8px;color:{theme.TEXT_DIM};
            font-family:'JetBrains Mono',monospace;line-height:1.4;
            background:transparent;
        """)
        self.hw_card.detail_layout.addWidget(self.hw_detail_lbl)

        scroll_layout.addWidget(self.hw_card)

        # Relay layer card
        self.relay_card = LayerCard("2", "Remote Access", "OFF", "relay")
        self.relay_card.setEnabled(False)
        self.relay_card.setStyleSheet(self.relay_card.styleSheet() + "QFrame{opacity:0.5;}")

        self._build_relay_controls()
        scroll_layout.addWidget(self.relay_card)
        scroll_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        # Pinned action button
        action_frame = QFrame()
        action_frame.setStyleSheet(f"background:{theme.SURFACE};border-top:1px solid {theme.BORDER};")
        af_layout = QVBoxLayout(action_frame)
        af_layout.setContentsMargins(12, 10, 12, 12)

        self.detect_btn = QPushButton("⟳  Detect Hardware")
        self.detect_btn.setStyleSheet(theme.BTN_PRIMARY)
        self.detect_btn.clicked.connect(self.start_detect)
        af_layout.addWidget(self.detect_btn)

        layout.addWidget(action_frame)
        return sidebar

    def _build_relay_controls(self):
        """Build relay config widgets inside relay_card.detail_layout."""
        dl = self.relay_card.detail_layout

        # Toggle row
        toggle_row = QHBoxLayout()
        toggle_lbl = QLabel("Relay Broadcast")
        toggle_lbl.setStyleSheet(f"font-size:8px;font-weight:700;color:{theme.TEXT_MID};background:transparent;")

        from PySide6.QtWidgets import QCheckBox
        self.relay_toggle = QCheckBox()
        self.relay_toggle.setStyleSheet(f"""
            QCheckBox::indicator {{ width:24px; height:14px; border-radius:7px;
                background:{theme.SURFACE}; border:1px solid {theme.BORDER}; }}
            QCheckBox::indicator:checked {{ background:{theme.TEAL}; border-color:{theme.TEAL}; }}
        """)
        self.relay_toggle.toggled.connect(self._on_relay_toggle)
        toggle_row.addWidget(toggle_lbl, stretch=1)
        toggle_row.addWidget(self.relay_toggle)
        dl.addLayout(toggle_row)

        # Relay config fields (hidden until toggle on)
        self.relay_config_frame = QFrame()
        self.relay_config_frame.hide()
        cfg_layout = QVBoxLayout(self.relay_config_frame)
        cfg_layout.setContentsMargins(0, 0, 0, 0)
        cfg_layout.setSpacing(4)

        def _field_label(text):
            l = QLabel(text.upper())
            l.setStyleSheet(f"font-size:8px;color:{theme.TEXT_DIM};font-weight:700;margin-top:6px;background:transparent;")
            return l

        cfg_layout.addWidget(_field_label("Relay Server URL"))
        self.relay_url = QLineEdit(os.environ.get("RELAY_URL", "ws://44.200.4.55:8765"))
        self.relay_url.setStyleSheet(theme.INPUT_FIELD)
        cfg_layout.addWidget(self.relay_url)

        cfg_layout.addWidget(_field_label("Microscope ID"))
        self.relay_room = QLineEdit(os.environ.get("MICROSCOPE_ID", "clinic-001"))
        self.relay_room.setStyleSheet(theme.INPUT_FIELD)
        cfg_layout.addWidget(self.relay_room)

        cfg_layout.addWidget(_field_label("API Key"))
        self.relay_pass = QLineEdit(os.environ.get("API_KEY", "mk-vyuhaa-2026"))
        self.relay_pass.setEchoMode(QLineEdit.Password)
        self.relay_pass.setPlaceholderText("••••••••")
        self.relay_pass.setStyleSheet(theme.INPUT_FIELD)
        cfg_layout.addWidget(self.relay_pass)

        self.relay_connect_btn = QPushButton("↗  Connect to Relay")
        self.relay_connect_btn.setStyleSheet(theme.BTN_PRIMARY)
        self.relay_connect_btn.clicked.connect(self.connect_relay)

        self.relay_disconnect_btn = QPushButton("Disconnect Relay")
        self.relay_disconnect_btn.setStyleSheet(theme.BTN_SECONDARY)
        self.relay_disconnect_btn.clicked.connect(self.disconnect_relay)

        cfg_layout.addSpacing(10)
        cfg_layout.addWidget(self.relay_connect_btn)
        cfg_layout.addSpacing(6)
        cfg_layout.addWidget(self.relay_disconnect_btn)

        dl.addWidget(self.relay_config_frame)

    def _build_main_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Mode banner
        self.mode_banner = QFrame()
        self.mode_banner.setObjectName("modeBanner")
        banner_layout = QHBoxLayout(self.mode_banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)

        self.mode_icon_lbl  = QLabel("⏸")
        self.mode_title_lbl = QLabel("Not Connected")
        self.mode_desc_lbl  = QLabel(
            "Searching for hardware. Relay broadcast unavailable until hardware is found."
        )
        self.mode_badge_lbl = QLabel("IDLE")
        self.mode_desc_lbl.setWordWrap(True)

        self.mode_icon_lbl.setStyleSheet("font-size:14px;background:transparent;")
        self.mode_title_lbl.setStyleSheet(f"font-family:'Syne',sans-serif;font-size:8px;font-weight:700;color:{theme.WHITE};background:transparent;")
        self.mode_desc_lbl.setStyleSheet(f"font-size:7px;color:{theme.TEXT_DIM};background:transparent;")

        mode_text = QVBoxLayout()
        mode_text.addWidget(self.mode_title_lbl)
        mode_text.addWidget(self.mode_desc_lbl)

        banner_layout.addWidget(self.mode_icon_lbl)
        banner_layout.addLayout(mode_text, stretch=1)
        banner_layout.addWidget(self.mode_badge_lbl)
        self._set_mode("idle")

        # Topology area (simple text labels for now)
        topo_area = QWidget()
        topo_layout = QVBoxLayout(topo_area)
        topo_layout.setAlignment(Qt.AlignCenter)
        topo_layout.addWidget(self.mode_banner)

        topo_diagram = QLabel(
            "[ Remote Clients ]  ──WS──  [ Relay Server ]  ──WSS──  [ This App ]  ──USB──  [ Microscope ]"
        )
        topo_diagram.setAlignment(Qt.AlignCenter)
        topo_diagram.setStyleSheet(f"font-family:'JetBrains Mono',monospace;font-size:9px;color:{theme.BORDER};background:transparent;")
        topo_layout.addWidget(topo_diagram)

        self.clients_lbl = QLabel("0 remote clients connected")
        self.clients_lbl.setAlignment(Qt.AlignCenter)
        self.clients_lbl.setStyleSheet(f"font-size:9px;color:{theme.TEXT_DIM};background:transparent;")
        topo_layout.addWidget(self.clients_lbl)

        layout.addWidget(topo_area, stretch=1)

        # Log strip
        log_strip = QFrame()
        log_strip.setFixedHeight(28) # Reduced from 36
        log_strip.setStyleSheet(f"QFrame{{background:{theme.SURFACE};border-top:1px solid {theme.BORDER};}}")
        log_layout = QHBoxLayout(log_strip)
        log_layout.setContentsMargins(20, 0, 20, 0)
        log_lbl = QLabel("LOG")
        log_lbl.setStyleSheet(f"font-family:'JetBrains Mono',monospace;font-size:8px;color:{theme.TEXT_DIM};letter-spacing:0.06em;background:transparent;")
        self.log_text = QLabel("→  Initialising hardware scanner…")
        self.log_text.setStyleSheet(f"font-family:'JetBrains Mono',monospace;font-size:9px;color:{theme.TEXT_MID};background:transparent;")
        log_layout.addWidget(log_lbl)
        log_layout.addWidget(self.log_text, stretch=1)
        layout.addWidget(log_strip)

        return panel

    # ── Hardware detection ────────────────────────────────────────────────────

    def start_detect(self):
        if self._hw_connected:
            return
        self.detect_btn.setText("⟳  Initialising…")
        self.detect_btn.setEnabled(False)
        self._set_log("→  Checking local environment for Vyuhaa modules…")
        self._set_mode("idle")

        # Start real discovery in a background thread
        self._worker = DiscoveryWorker()
        self._worker.discovery_event.connect(self._on_discovery_event)
        self._worker.connection_result.connect(self._on_connection_result)
        self._worker.error_occurred.connect(self._on_discovery_error)
        self._worker.start()

    def _on_discovery_event(self, devices: list[ofa.DeviceInfo]):
        if not devices:
            self._set_log("✕  No devices found. Ensure microscope is connected.")
            self.detect_btn.setText("⟳  Initialise Hardware")
            self.detect_btn.setEnabled(True)
            self.hw_card.set_status("SEARCHING", "disconnected")
        else:
            self._set_log(f"→  Found {devices[0].name}. Connecting and initializing...")

    def _on_connection_result(self, success: bool, device: ofa.DeviceInfo):
        if success:
            self._on_hw_found(device)
        else:
            self._set_log("✕  Found device but failed to initialise hardware.")
            self.detect_btn.setText("⟳  Initialise Hardware")
            self.detect_btn.setEnabled(True)
            self.hw_card.set_status("SEARCHING", "disconnected")

    def _on_discovery_error(self, err: str):
        self._set_log(f"✕  Discovery error: {err}")
        self.detect_btn.setText("⟳  Initialise Hardware")
        self.detect_btn.setEnabled(True)

    def _on_hw_found(self, device: ofa.DeviceInfo):
        """Called when hardware is successfully connected."""
        self._hw_connected = True
        device.connected = True

        self.hw_card.set_status("CONNECTED", "connected")
        self.hw_detail_lbl.setText(
            f"<b>{device.name}</b><br>"
            f"Direct Python Access · Zero Latency"
        )
        self.relay_card.setEnabled(True)
        self.relay_card.setStyleSheet("")

        self.detect_btn.setText("✕  Disconnect Hardware")
        self.detect_btn.setEnabled(True)
        self.detect_btn.setStyleSheet(theme.BTN_DANGER)
        self.detect_btn.clicked.disconnect()
        self.detect_btn.clicked.connect(self.disconnect_hardware)

        self._set_mode("direct")
        self._set_log(f"✓  Hardware connected → {device.name}")
        self.hw_connected.emit(device)

    def disconnect_hardware(self):
        # [VYUHAA API] call ofa.disconnect() here
        try:
            ofa.disconnect()
        except:
            pass
        self._hw_connected = False
        if self._relay_connected:
            self.disconnect_relay()

        self.hw_card.set_status("SEARCHING", "disconnected")
        self.hw_detail_lbl.setText("No device found\nChecking local environment for Vyuhaa modules…")
        self.relay_card.setEnabled(False)

        self.detect_btn.setText("⟳  Initialise Hardware")
        self.detect_btn.setEnabled(True)
        self.detect_btn.setStyleSheet(theme.BTN_PRIMARY)
        self.detect_btn.clicked.disconnect()
        self.detect_btn.clicked.connect(self.start_detect)

        self._set_mode("idle")
        self._set_log("Hardware disconnected. Preparing for initialisation…")
        self.hw_disconnected.emit()

    # ── Relay ─────────────────────────────────────────────────────────────────

    def _on_relay_toggle(self, checked: bool):
        self.relay_config_frame.setVisible(checked)
        if not checked:
            self.disconnect_relay()

    def connect_relay(self):
        if not self._hw_connected:
            self._set_log("✕  Cannot start relay without hardware connection.")
            return
        self.relay_connect_btn.setText("↗  Connecting…")
        self.relay_connect_btn.setEnabled(False)
        self.relay_card.set_status("CONNECTING", "idle")
        
        url = self.relay_url.text()
        room = self.relay_room.text()
        password = self.relay_pass.text()
        
        self._set_log(f"→  Connecting to relay server at {url}…")
        
        # [VYUHAA API] Real connection call
        if ofa.relay_connect(url, room, password):
            self._on_relay_connected()
        else:
            self._set_log("✕  Relay connection failed. Check URL and credentials.")
            self.relay_card.set_status("OFF", "idle")
            self.relay_connect_btn.setText("↗  Connect to Relay")
            self.relay_connect_btn.setEnabled(True)

    def _on_relay_connected(self):
        self._relay_connected = True
        room = self.relay_room.text()
        self.relay_card.set_status("ACTIVE", "active")
        self.relay_connect_btn.setText("✓  Relay Connected")
        self.relay_connect_btn.setEnabled(False)
        self._set_mode("relay")
        self._set_log(f"✓  Relay active — broadcasting on room: {room}")
        
        # Start status polling
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._update_relay_status)
        self._poll_timer.start(3000)
        
        self.relay_connected.emit(room)

    def _update_relay_status(self):
        if not self._relay_connected:
            if hasattr(self, "_poll_timer"): self._poll_timer.stop()
            return
        status = ofa.get_relay_status()
        count = status.get("client_count", 0)
        self.clients_lbl.setText(f"{count} remote clients connected")

    def disconnect_relay(self):
        # [VYUHAA API] Real disconnect call
        ofa.relay_disconnect()
        self._relay_connected = False
        if hasattr(self, "_poll_timer"): self._poll_timer.stop()
        
        self.relay_card.set_status("OFF", "idle")
        self.relay_connect_btn.setText("↗  Connect to Relay")
        self.relay_connect_btn.setEnabled(True)
        self.clients_lbl.setText("0 remote clients connected")
        
        if self._hw_connected:
            self._set_mode("direct")
        else:
            self._set_mode("idle")
        self._set_log("Relay disconnected. Running in direct hardware mode.")
        self.relay_disconnected.emit()

    # ── Mode banner helpers ───────────────────────────────────────────────────

    def _set_mode(self, mode: str):
        modes = {
            "idle": {
                "icon": "⏸", "title": "Not Connected",
                "desc": "Searching for hardware. Relay broadcast unavailable until hardware is found.",
                "badge": "IDLE",
                "badge_style": f"background:{theme.SURFACE};color:{theme.TEXT_DIM};border:1px solid {theme.BORDER};",
                "banner_style": f"background:{theme.SURFACE2};border:1px solid {theme.BORDER};",
            },
            "direct": {
                "icon": "🔗", "title": "Direct Hardware Mode",
                "desc": "App controls hardware directly. No relay running. Enable Remote Access to allow remote clients.",
                "badge": "DIRECT",
                "badge_style": f"background:rgba(34,197,94,0.12);color:{theme.GREEN};border:1px solid rgba(34,197,94,0.3);",
                "banner_style": f"background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.3);",
            },
            "relay": {
                "icon": "📡", "title": "Relay Broadcast Mode",
                "desc": "App controls hardware AND acts as relay endpoint. Remote clients stream data through this app.",
                "badge": "RELAY ACTIVE",
                "badge_style": f"background:rgba(30,184,168,0.10);color:{theme.TEAL};border:1px solid rgba(30,184,168,0.3);",
                "banner_style": f"background:rgba(30,184,168,0.10);border:1px solid rgba(30,184,168,0.3);",
            },
        }
        m = modes.get(mode, modes["idle"])
        self.mode_icon_lbl.setText(m["icon"])
        self.mode_title_lbl.setText(m["title"])
        self.mode_desc_lbl.setText(m["desc"])
        self.mode_badge_lbl.setText(m["badge"])
        self.mode_badge_lbl.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace;font-size:8px;font-weight:600;"
            f"letter-spacing:0.06em;padding:3px 10px;border-radius:8px;{m['badge_style']}"
        )
        self.mode_banner.setStyleSheet(
            f"QFrame{{border-radius:10px;{m['banner_style']}}}"
        )

    def _set_log(self, text: str):
        self.log_text.setText(text)