"""
Vyuhaa Remote Client — Connect Page
Two-panel: sidebar (relay + device config) + main (topology diagram + log).
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QLineEdit, QCheckBox, QSizePolicy, QScrollArea,
    QSpacerItem
)
from PySide6.QtCore import Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from styles import TEAL, GREEN, RED, AMBER, BORDER, SURFACE, SURFACE2, BG, TEXT_DIM, TEXT_MID, WHITE


# ── Topology node widget ──────────────────────────────────────────────────────

class NodeWidget(QWidget):
    """Visual node (box + label) in the topology diagram."""

    def __init__(self, icon: str, name: str, sub: str = "",
                 style: str = "default", parent=None):
        super().__init__(parent)
        self._lit   = False
        self._hw    = False
        self._error = False
        self.setFixedSize(120, 130)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        self.box = QFrame()
        self.box.setFixedSize(106, 90)
        box_layout = QVBoxLayout(self.box)
        box_layout.setAlignment(Qt.AlignCenter)
        box_layout.setSpacing(5)

        self.icon_lbl = QLabel(icon)
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        self.icon_lbl.setStyleSheet("font-size: 24px; background: transparent;")
        self.name_lbl = QLabel(name)
        self.name_lbl.setAlignment(Qt.AlignCenter)
        self.name_lbl.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #94a3b8; "
            "background: transparent; line-height: 1.3;"
        )
        box_layout.addWidget(self.icon_lbl)
        box_layout.addWidget(self.name_lbl)

        self.sub_lbl = QLabel(sub)
        self.sub_lbl.setAlignment(Qt.AlignCenter)
        self.sub_lbl.setStyleSheet(
            "font-size: 11px; color: #64748b; "
            "font-family: 'JetBrains Mono', monospace; "
            "background: transparent;"
        )
        self.sub_lbl.setWordWrap(True)
        self.sub_lbl.setFixedWidth(120)

        if style == "this":
            self.box.setStyleSheet(
                f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f"stop:0 rgba(30,184,168,0.08),stop:1 {SURFACE2});"
                f"border: 1px solid rgba(30,184,168,0.5);"
                f"border-radius: 16px;"
            )
        else:
            self._apply_dim()

        layout.addWidget(self.box, alignment=Qt.AlignCenter)
        layout.addWidget(self.sub_lbl, alignment=Qt.AlignCenter)

    def set_lit(self, lit: bool, hw: bool = False, error: bool = False):
        self._lit   = lit
        self._hw    = hw
        self._error = error
        if error:
            self.box.setStyleSheet(
                f"background: rgba(239,68,68,0.06); border: 1px solid rgba(239,68,68,0.4);"
                f"border-radius: 16px; opacity: 1;"
            )
        elif lit and hw:
            self.box.setStyleSheet(
                f"background: rgba(34,197,94,0.06); border: 1px solid rgba(34,197,94,0.5);"
                f"border-radius: 16px; opacity: 1;"
            )
        elif lit:
            self.box.setStyleSheet(
                f"background: rgba(30,184,168,0.06); border: 1px solid rgba(30,184,168,0.5);"
                f"border-radius: 16px; opacity: 1;"
            )
        else:
            self._apply_dim()
        self.setEnabled(lit)
        self.box.setEnabled(lit)

    def _apply_dim(self):
        self.box.setStyleSheet(
            f"background: {SURFACE2}; border: 1px solid {BORDER}; "
            f"border-radius: 16px; opacity: 0.3;"
        )

    def update_sub(self, text: str):
        self.sub_lbl.setText(text)


# ── Status badge ─────────────────────────────────────────────────────────────

class LayerStatusBadge(QLabel):
    STYLES = {
        "off":        ("OFF",        "rgba(100,116,139,0.15)", "#64748b", "#2a3441"),
        "connecting": ("CONNECTING", "rgba(245,158,11,0.12)",  "#f59e0b", "rgba(245,158,11,0.3)"),
        "connected":  ("CONNECTED",  "rgba(30,184,168,0.10)",  "#1eb8a8", "rgba(30,184,168,0.3)"),
        "live":       ("LIVE",       "rgba(34,197,94,0.12)",   "#22c55e", "rgba(34,197,94,0.3)"),
        "error":      ("ERROR",      "rgba(239,68,68,0.12)",   "#ef4444", "rgba(239,68,68,0.3)"),
        "offline":    ("OFFLINE",    "rgba(100,116,139,0.15)", "#64748b", "#2a3441"),
    }

    def __init__(self, state: str = "off", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 600; "
                           "letter-spacing: 1px; padding: 2px 8px; border-radius: 10px;")
        self.set_state(state)

    def set_state(self, state: str):
        txt, bg, fg, border = self.STYLES.get(state, self.STYLES["off"])
        self.setText(txt)
        self.setStyleSheet(
            f"font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 600; "
            f"letter-spacing: 1px; padding: 2px 8px; border-radius: 10px; "
            f"background: {bg}; color: {fg}; border: 1px solid {border};"
        )


# ── Mode banner ───────────────────────────────────────────────────────────────

class ModeBanner(QFrame):
    MODES = {
        "idle":       ("⏸",  "Not Connected",     "Enter relay credentials and press Connect to begin.", "idle"),
        "connecting": ("⟳",  "Connecting…",       "Establishing WebSocket connection to relay server.", "connecting"),
        "relay":      ("☁️",  "Relay Connected",   "Relay OK — waiting for device agent to come online.", "relay"),
        "live":       ("✓",   "Fully Connected",   "Relay and microscope hardware are both live.", "live"),
        "error":      ("✕",   "Connection Error",  "Check credentials and network, then retry.", "error"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        self.icon_lbl  = QLabel("⏸")
        self.icon_lbl.setStyleSheet("font-size: 18px; background: transparent;")
        self.title_lbl = QLabel("Not Connected")
        self.title_lbl.setStyleSheet(
            "font-size: 13px; font-weight: 700; color: #ffffff; background: transparent;"
        )
        self.desc_lbl  = QLabel("Enter relay credentials and press Connect to begin.")
        self.desc_lbl.setStyleSheet(
            "font-size: 11px; color: #64748b; background: transparent;"
        )
        self.badge     = LayerStatusBadge("off")

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.addWidget(self.title_lbl)
        text_col.addWidget(self.desc_lbl)

        lay.addWidget(self.icon_lbl)
        lay.addLayout(text_col)
        lay.addStretch()
        lay.addWidget(self.badge)

        self.set_mode("idle")

    def set_mode(self, mode: str):
        icon, title, desc, badge_state = self.MODES.get(mode, self.MODES["idle"])
        self.icon_lbl.setText(icon)
        self.title_lbl.setText(title)
        self.desc_lbl.setText(desc)
        self.badge.set_state(badge_state)

        bg_map = {
            "idle":       (SURFACE2, BORDER),
            "connecting": ("rgba(245,158,11,0.12)", "rgba(245,158,11,0.3)"),
            "relay":      ("rgba(30,184,168,0.08)",  "rgba(30,184,168,0.3)"),
            "live":       ("rgba(34,197,94,0.08)",   "rgba(34,197,94,0.3)"),
            "error":      ("rgba(239,68,68,0.08)",   "rgba(239,68,68,0.3)"),
        }
        bg, border = bg_map.get(mode, (SURFACE2, BORDER))
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
        )


# ── ConnLine widget (animated data line) ────────────────────────────────────

class ConnLine(QWidget):
    def __init__(self, label: str = "", hw: bool = False, parent=None):
        super().__init__(parent)
        self._lit   = False
        self._hw    = hw
        self._label = label
        self.setFixedHeight(50)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Packet animation timer
        self._anim_pos  = 0.0
        self._anim_pos2 = 0.5
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def set_lit(self, lit: bool):
        self._lit = lit
        if lit:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._anim_pos  = (self._anim_pos  + 0.018) % 1.0
        self._anim_pos2 = (self._anim_pos2 + 0.018) % 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        mid  = h // 2

        # Line
        if self._lit:
            colour = QColor(GREEN) if self._hw else QColor(TEAL)
            pen = QPen(colour, 2)
        else:
            pen = QPen(QColor(BORDER), 2)
        p.setPen(pen)
        p.drawLine(0, mid, w, mid)

        # Label
        lbl_colour = QColor(GREEN if (self._lit and self._hw) else
                            TEAL  if self._lit else TEXT_DIM)
        p.setPen(QPen(lbl_colour))
        p.setFont(QFont("JetBrains Mono", 9))
        p.drawText(0, mid - 6, self._label)

        # Animated packets
        if self._lit:
            dot_colour = QColor(GREEN) if self._hw else QColor(TEAL)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(dot_colour))
            for pos in (self._anim_pos, self._anim_pos2):
                x = int(pos * w) - 4
                p.drawEllipse(x, mid - 4, 8, 8)
        p.end()


# ── Connect Page ──────────────────────────────────────────────────────────────

class ConnectPage(QWidget):
    """
    Signals
    -------
    connect_requested(url, room, key, auto_reconnect)
    disconnect_requested()
    """

    connect_requested    = Signal(str, str, str, bool)
    disconnect_requested = Signal()
    home_requested       = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setObjectName("SideBar")
        sidebar.setFixedWidth(280)
        sidebar_v = QVBoxLayout(sidebar)
        sidebar_v.setContentsMargins(0, 0, 0, 0)
        sidebar_v.setSpacing(0)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_w = QWidget()
        sv = QVBoxLayout(scroll_w)
        sv.setContentsMargins(20, 24, 20, 16)
        sv.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title = QLabel("Connect")
        title.setObjectName("SidebarTitle")

        self.home_btn = QPushButton("Home")
        self.home_btn.setObjectName("HomeBtn")
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.setFixedHeight(34)
        self.home_btn.setMinimumWidth(128)
        self.home_btn.clicked.connect(self.home_requested.emit)

        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.home_btn, alignment=Qt.AlignVCenter)
        sv.addLayout(title_row)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER}; background: {BORDER}; border: none; max-height: 1px;")
        sv.addWidget(sep)
        sv.addSpacing(10)

        sub = QLabel("Connect to your relay server, then reach the remote device and its microscope hardware.")
        sub.setObjectName("SidebarSub")
        sub.setWordWrap(True)
        sv.addWidget(sub)
        sv.addSpacing(14)

        # Layer 1 — Relay
        sv.addWidget(self._build_relay_layer())
        sv.addSpacing(10)
        # Layer 2 — Device
        sv.addWidget(self._build_device_layer())
        sv.addStretch()

        scroll.setWidget(scroll_w)
        sidebar_v.addWidget(scroll)

        # Sidebar action buttons
        actions_bar = QFrame()
        actions_bar.setObjectName("SidebarActionsBar")
        actions_bar.setFixedHeight(80)
        ab = QVBoxLayout(actions_bar)
        ab.setContentsMargins(20, 12, 20, 12)
        ab.setSpacing(8)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.connect_btn    = QPushButton("↗   Connect")
        self.connect_btn.setObjectName("BtnPrimary")
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.setFixedHeight(44)
        self.connect_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        self.disconnect_btn = QPushButton("✕   Disconnect")
        self.disconnect_btn.setObjectName("BtnDanger")
        self.disconnect_btn.setCursor(Qt.PointingHandCursor)
        self.disconnect_btn.setFixedHeight(44)
        self.disconnect_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.disconnect_btn.setVisible(False)
        self.disconnect_btn.clicked.connect(self.disconnect_requested.emit)

        action_row.addWidget(self.connect_btn, 1)
        action_row.addWidget(self.disconnect_btn, 1)
        ab.addLayout(action_row)
        sidebar_v.addWidget(actions_bar)

        root.addWidget(sidebar)

        # ── Main panel ────────────────────────────────────────────────────
        main = QWidget()
        main_v = QVBoxLayout(main)
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)

        topo_area = QWidget()
        topo_v = QVBoxLayout(topo_area)
        topo_v.setContentsMargins(24, 24, 24, 16)
        topo_v.setAlignment(Qt.AlignCenter)

        self.mode_banner = ModeBanner()
        topo_v.addWidget(self.mode_banner)
        topo_v.addSpacing(24)

        # Topology row
        topo_row = QHBoxLayout()
        topo_row.setSpacing(0)
        topo_row.setAlignment(Qt.AlignCenter)

        self.node_client = NodeWidget("🖥️", "This\nClient",  "Remote Viewer", "this")
        self.line1       = ConnLine("WebSocket / TLS")
        self.node_relay  = NodeWidget("☁️", "Relay\nServer",  "44.200.4.55:8765")
        self.line2       = ConnLine("Encrypted WS")
        self.node_agent  = NodeWidget("💻", "Device\nAgent",   "clinic-001")
        self.line3       = ConnLine("USB / Serial", hw=True)
        self.node_hw     = NodeWidget("🔬", "Microscope\nHW",  "Jetson Hardware")

        for w in (self.node_client, self.line1, self.node_relay,
                  self.line2, self.node_agent, self.line3, self.node_hw):
            topo_row.addWidget(w)

        topo_v.addLayout(topo_row)
        topo_v.addStretch()
        main_v.addWidget(topo_area, stretch=1)

        # Log strip
        log_strip = QFrame()
        log_strip.setObjectName("LogStrip")
        log_strip.setFixedHeight(36)
        log_h = QHBoxLayout(log_strip)
        log_h.setContentsMargins(20, 0, 20, 0)
        log_h.setSpacing(10)

        log_label = QLabel("LOG")
        log_label.setStyleSheet(
            "font-family: 'JetBrains Mono', monospace; font-size: 10px; "
            "color: #64748b; letter-spacing: 2px; background: transparent;"
        )
        self.log_text = QLabel("Ready to connect.")
        self.log_text.setObjectName("LogText")

        log_h.addWidget(log_label)
        log_h.addWidget(self.log_text)
        log_h.addStretch()
        main_v.addWidget(log_strip)

        root.addWidget(main, stretch=1)

    def _build_relay_layer(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("ConnLayerActive")
        frame.setStyleSheet(
            "QFrame#ConnLayerActive { background: #1c2330; border: 1px solid rgba(30,184,168,0.45); "
            "border-radius: 14px; }"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(6)

        # Header
        h = QHBoxLayout()
        num = QLabel("1")
        num.setFixedSize(22, 22)
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(
            "background: rgba(30,184,168,0.10); color: #1eb8a8; "
            "border: 1px solid rgba(30,184,168,0.3); border-radius: 11px; "
            "font-size: 10px; font-weight: 700;"
        )
        ltitle = QLabel("Relay Server")
        ltitle.setStyleSheet("font-size: 13px; font-weight: 700; color: #e2e8f0; background: transparent;")
        self.relay_status_badge = LayerStatusBadge("off")
        h.addWidget(num); h.addWidget(ltitle); h.addStretch(); h.addWidget(self.relay_status_badge)
        v.addLayout(h)

        # Fields
        url_lbl = QLabel("SERVER URL (ws:// direct or wss:// relay)")
        url_lbl.setObjectName("FieldLabel")
        url_lbl.setWordWrap(True)
        self.relay_url = QLineEdit("ws://44.200.4.55:8765")
        room_lbl = QLabel("USERNAME (relay) / ROOM ID (direct)")
        room_lbl.setObjectName("FieldLabel")
        room_lbl.setWordWrap(True)
        self.relay_room = QLineEdit("admin")
        self.relay_room.setPlaceholderText("e.g. john  (relay username) or lab-01 (room)")
        key_lbl = QLabel("PASSWORD (relay) / ACCESS KEY (direct)")
        key_lbl.setObjectName("FieldLabel")
        key_lbl.setWordWrap(True)
        self.relay_key  = QLineEdit("vyuhaa2026")
        self.relay_key.setPlaceholderText("Leave blank for direct ws:// connection")
        self.relay_key.setEchoMode(QLineEdit.Password)

        self.auto_reconnect = QCheckBox("Auto-reconnect on drop")
        self.auto_reconnect.setChecked(True)

        for w in (url_lbl, self.relay_url, room_lbl, self.relay_room,
                  key_lbl, self.relay_key, self.auto_reconnect):
            v.addWidget(w)
        return frame

    def _build_device_layer(self) -> QFrame:
        self._device_layer = QFrame()
        self._device_layer.setObjectName("ConnLayer")
        self._device_layer.setStyleSheet(
            "QFrame#ConnLayer { background: #1c2330; border: 1px solid #2a3441; border-radius: 14px; }"
        )
        self._device_layer.setEnabled(False)
        v = QVBoxLayout(self._device_layer)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(6)

        h = QHBoxLayout()
        num = QLabel("2")
        num.setFixedSize(22, 22)
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(
            "background: rgba(34,197,94,0.10); color: #22c55e; "
            "border: 1px solid rgba(34,197,94,0.3); border-radius: 11px; "
            "font-size: 10px; font-weight: 700;"
        )
        ltitle = QLabel("Remote Device")
        ltitle.setStyleSheet("font-size: 13px; font-weight: 700; color: #e2e8f0; background: transparent;")
        self.device_status_badge = LayerStatusBadge("offline")
        h.addWidget(num); h.addWidget(ltitle); h.addStretch(); h.addWidget(self.device_status_badge)
        v.addLayout(h)

        self.device_detail = QLabel(
            "<strong style='color:#94a3b8'>Waiting for relay connection</strong><br>"
            "Hardware bridge unavailable until relay is active."
        )
        self.device_detail.setStyleSheet(
            "font-size: 12px; color: #64748b; "
            "font-family: 'JetBrains Mono', monospace; "
            "background: transparent; line-height: 1.6;"
        )
        self.device_detail.setTextFormat(Qt.RichText)
        self.device_detail.setWordWrap(True)
        v.addWidget(self.device_detail)
        return self._device_layer

    # ── Slots / public API ────────────────────────────────────────────────

    def _on_connect_clicked(self):
        self.connect_requested.emit(
            self.relay_url.text(),
            self.relay_room.text(),
            self.relay_key.text(),
            self.auto_reconnect.isChecked()
        )

    def update_log(self, text: str):
        self.log_text.setText(text)

    def set_relay_connecting(self):
        self.relay_status_badge.set_state("connecting")
        self.mode_banner.set_mode("connecting")
        self.connect_btn.setEnabled(False)

    def set_relay_connected(self):
        self.relay_status_badge.set_state("connected")
        self.node_relay.set_lit(True)
        self.line1.set_lit(True)
        self.mode_banner.set_mode("relay")
        self._device_layer.setEnabled(True)
        self._device_layer.setStyleSheet(
            "QFrame#ConnLayer { background: #1c2330; border: 1px solid rgba(34,197,94,0.35); border-radius: 14px; }"
        )
        self.device_status_badge.set_state("connecting")
        self.connect_btn.setVisible(False)
        self.disconnect_btn.setVisible(True)

    def set_live(self):
        self.relay_status_badge.set_state("live")
        self.device_status_badge.set_state("live")
        self.node_relay.set_lit(True)
        self.node_agent.set_lit(True)
        self.node_hw.set_lit(True, hw=True)
        self.line2.set_lit(True)
        self.line3.set_lit(True)
        self.mode_banner.set_mode("live")
        self.device_detail.setText(
            "<strong style='color:#22c55e'>Device agent online</strong><br>"
            "Microscope hardware connected and ready."
        )

    def set_disconnected(self):
        self.relay_status_badge.set_state("off")
        self.device_status_badge.set_state("offline")
        for node in (self.node_relay, self.node_agent, self.node_hw):
            node.set_lit(False)
        for line in (self.line1, self.line2, self.line3):
            line.set_lit(False)
        self.mode_banner.set_mode("idle")
        self._device_layer.setEnabled(False)
        self._device_layer.setStyleSheet(
            "QFrame#ConnLayer { background: #1c2330; border: 1px solid #2a3441; border-radius: 14px; }"
        )
        self.device_detail.setText(
            "<strong style='color:#94a3b8'>Waiting for relay connection</strong><br>"
            "Hardware bridge unavailable until relay is active."
        )
        self.connect_btn.setEnabled(True)
        self.connect_btn.setVisible(True)
        self.disconnect_btn.setVisible(False)

    def set_error(self, msg: str):
        self.relay_status_badge.set_state("error")
        self.mode_banner.set_mode("error")
        self.connect_btn.setEnabled(True)
        self.update_log(f"✕  {msg}")

    def update_relay_url_label(self, url: str):
        self.node_relay.update_sub(url.replace("wss://", "").split("/")[0])

    def update_device_label(self, room: str):
        self.node_agent.update_sub(room)
