"""
Vyuhaa Remote Client — Settings Page
Two-panel: sidebar nav (5 sections) + main (stacked panels).
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QSizePolicy, QScrollArea, QLineEdit,
    QComboBox, QCheckBox, QSpinBox, QStackedWidget
)
from PySide6.QtCore import Qt, Signal
from styles import TEAL, GREEN, RED, AMBER, BORDER, SURFACE, SURFACE2, BG, TEXT_DIM, TEXT_MID, WHITE


# ── Settings row widget ───────────────────────────────────────────────────────

class SettingsRow(QFrame):
    def __init__(self, icon: str, label: str, desc: str = "",
                 control: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{SURFACE2}; border:1px solid {BORDER}; border-radius:10px; }}"
        )
        h = QHBoxLayout(self); h.setContentsMargins(14, 10, 14, 10); h.setSpacing(14)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size:18px; background:transparent;")
        icon_lbl.setFixedWidth(26)

        text_col = QVBoxLayout(); text_col.setSpacing(2)
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("font-size:13px; font-weight:600; color:#e2e8f0; background:transparent;")
        text_col.addWidget(name_lbl)
        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(f"font-size:11px; color:{TEXT_DIM}; background:transparent;")
            text_col.addWidget(desc_lbl)

        h.addWidget(icon_lbl)
        h.addLayout(text_col, 1)
        if control:
            h.addWidget(control)


# ── Section base ──────────────────────────────────────────────────────────────

class SettingsSection(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.v = QVBoxLayout(inner)
        self.v.setContentsMargins(24, 24, 24, 24)
        self.v.setSpacing(10)
        self.setWidget(inner)

    def _title(self, text: str, sub: str = ""):
        t = QLabel(text)
        t.setStyleSheet("font-size:16px; font-weight:700; color:#fff; background:transparent;")
        self.v.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setStyleSheet(f"font-size:12px; color:{TEXT_DIM}; background:transparent;")
            s.setWordWrap(True)
            self.v.addWidget(s)
        self.v.addSpacing(4)

    def _section_label(self, text: str):
        l = QLabel(text)
        l.setStyleSheet(
            f"font-size:10px; font-family:'JetBrains Mono',monospace; letter-spacing:2px; "
            f"font-weight:700; color:{TEXT_DIM}; background:transparent;"
        )
        self.v.addWidget(l)


# ── Connection section ────────────────────────────────────────────────────────

class ConnectionSection(SettingsSection):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title("Connection", "Relay server settings and authentication credentials.")
        self._section_label("RELAY SERVER")

        self.url_input = QLineEdit("wss://relay.vyuhaa.com")
        self.v.addWidget(SettingsRow("☁️", "Server URL", "WebSocket relay endpoint", self.url_input))

        self.room_input = QLineEdit("lab-hyderabad-01")
        self.v.addWidget(SettingsRow("🔑", "Room / Session ID", "Microscope room identifier", self.room_input))

        auto_cb = QCheckBox()
        auto_cb.setChecked(True)
        self.v.addWidget(SettingsRow("↺", "Auto-Reconnect", "Reconnect automatically on drop", auto_cb))

        timeout_spin = QSpinBox()
        timeout_spin.setRange(5, 120); timeout_spin.setValue(30); timeout_spin.setSuffix(" s")
        self.v.addWidget(SettingsRow("⏱", "Connection Timeout", "Max seconds before giving up", timeout_spin))

        tls_cb = QCheckBox()
        tls_cb.setChecked(True)
        self.v.addWidget(SettingsRow("🔒", "Verify TLS Certificate", "Disable for self-signed certs", tls_cb))
        self.v.addStretch()


# ── Streaming section ─────────────────────────────────────────────────────────

class StreamingSection(SettingsSection):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title("Streaming", "Video quality and codec settings for live view.")
        self._section_label("VIDEO QUALITY")

        q_combo = QComboBox()
        q_combo.addItems(["SD (480p)","HD (720p)","FHD (1080p)","4K (2160p)"])
        q_combo.setCurrentIndex(1)
        self.v.addWidget(SettingsRow("📡", "Default Quality", "Stream resolution at connect", q_combo))

        fps_combo = QComboBox()
        fps_combo.addItems(["15 fps","24 fps","30 fps","60 fps"]); fps_combo.setCurrentIndex(2)
        self.v.addWidget(SettingsRow("🎞", "Frame Rate", "Target frames per second", fps_combo))

        codec_combo = QComboBox()
        codec_combo.addItems(["H.264","H.265 / HEVC","VP9","AV1"]); codec_combo.setCurrentIndex(0)
        self.v.addWidget(SettingsRow("⚙️", "Video Codec", "Encoding codec preference", codec_combo))

        abr_cb = QCheckBox(); abr_cb.setChecked(True)
        self.v.addWidget(SettingsRow("📶", "Adaptive Bitrate", "Auto-adjust quality to bandwidth", abr_cb))
        self.v.addStretch()


# ── Stage control section ─────────────────────────────────────────────────────

class StageSection(SettingsSection):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title("Stage Control", "Movement steps, speed, and axis inversion.")
        self._section_label("STEP SIZES")

        for label, default in (("Fine Step (1-click)","1 μm"),
                                ("Medium Step",        "10 μm"),
                                ("Coarse Step",        "100 μm")):
            combo = QComboBox()
            combo.addItems(["1 μm","10 μm","50 μm","100 μm","500 μm","1 mm"])
            combo.setCurrentText(default)
            self.v.addWidget(SettingsRow("↔", label, "", combo))

        self._section_label("AXIS")
        ix = QCheckBox(); ix.setChecked(False)
        self.v.addWidget(SettingsRow("↔", "Invert X", "Reverse horizontal direction", ix))
        iy = QCheckBox(); iy.setChecked(False)
        self.v.addWidget(SettingsRow("↕", "Invert Y", "Reverse vertical direction", iy))

        spd = QComboBox()
        spd.addItems(["Slow (0.5×)","Normal (1×)","Fast (2×)"]); spd.setCurrentIndex(1)
        self.v.addWidget(SettingsRow("⚡", "Stage Acceleration", "Movement speed profile", spd))
        self.v.addStretch()


# ── Shortcuts section ─────────────────────────────────────────────────────────

class ShortcutsSection(SettingsSection):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title("Keyboard Shortcuts", "Default key bindings for live view control.")
        self._section_label("STAGE MOVEMENT")

        for direction, default_key in (
            ("Move Up",    "W"),
            ("Move Down",  "S"),
            ("Move Left",  "A"),
            ("Move Right", "D"),
            ("Focus Up",   "+"),
            ("Focus Down", "−"),
        ):
            key_input = QLineEdit(default_key)
            key_input.setFixedWidth(50)
            key_input.setAlignment(Qt.AlignCenter)
            self.v.addWidget(SettingsRow("⌨", direction, "", key_input))

        self._section_label("CAPTURE")
        cap = QLineEdit("Space"); cap.setFixedWidth(80); cap.setAlignment(Qt.AlignCenter)
        self.v.addWidget(SettingsRow("📷", "Capture Frame", "", cap))
        self.v.addStretch()


# ── About section ─────────────────────────────────────────────────────────────

class AboutSection(SettingsSection):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title("About Vyuhaa Remote Client")

        info_frame = QFrame()
        info_frame.setStyleSheet(
            f"background:{SURFACE2}; border:1px solid {BORDER}; border-radius:12px;"
        )
        fv = QVBoxLayout(info_frame); fv.setContentsMargins(16, 14, 16, 14); fv.setSpacing(10)
        for label, val in (
            ("Application",     "Vyuhaa Remote Client"),
            ("Version",         "v3.0.0"),
            ("Device Profile",  "OpenFlexure LBC — Jetson Nano"),
            ("Protocol",        "WebSocket + WebRTC DataChannel"),
            ("Build",           "PySide6 / Qt 6.x"),
        ):
            row = QHBoxLayout()
            l = QLabel(label)
            l.setStyleSheet(f"font-size:12px; color:{TEXT_DIM}; background:transparent;")
            v_lbl = QLabel(val)
            v_lbl.setStyleSheet(
                f"font-family:'JetBrains Mono',monospace; font-size:12px; "
                f"color:#e2e8f0; background:transparent; font-weight:600;"
            )
            row.addWidget(l); row.addStretch(); row.addWidget(v_lbl)
            fv.addLayout(row)
        self.v.addWidget(info_frame)

        docs_btn = QPushButton("↗   View Documentation")
        docs_btn.setObjectName("BtnSecondary"); docs_btn.setFixedHeight(40)
        docs_btn.setCursor(Qt.PointingHandCursor)
        self.v.addWidget(docs_btn)
        self.v.addStretch()


# ── Settings Page ─────────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    saved = Signal()
    reset = Signal()
    home_requested = Signal()

    NAV = [
        ("🔗", "Connection"),
        ("📡", "Streaming"),
        ("↔", "Stage Control"),
        ("⌨", "Shortcuts"),
        ("ℹ", "About"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nav_btns: list[QPushButton] = []
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────────
        sidebar = QFrame(); sidebar.setObjectName("SideBar"); sidebar.setFixedWidth(240)
        sv = QVBoxLayout(sidebar); sv.setContentsMargins(0,0,0,0); sv.setSpacing(0)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        sw = QWidget(); sv2 = QVBoxLayout(sw); sv2.setContentsMargins(16,24,16,16); sv2.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Settings"); title.setObjectName("SidebarTitle")
        self.home_btn = QPushButton("Home")
        self.home_btn.setObjectName("HomeBtn")
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.setFixedHeight(34)
        self.home_btn.setMinimumWidth(128)
        self.home_btn.clicked.connect(self.home_requested.emit)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.home_btn, alignment=Qt.AlignVCenter)
        sv2.addLayout(title_row)
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{BORDER};max-height:1px;border:none;"); sv2.addWidget(sep)
        sv2.addSpacing(8)

        for i, (icon, label) in enumerate(self.NAV):
            b = QPushButton(f"{icon}  {label}")
            b.setObjectName("SettingsNavItemActive" if i==0 else "SettingsNavItem")
            b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
            b.setFixedHeight(40); b.clicked.connect(lambda _, idx=i: self._nav(idx))
            self._nav_btns.append(b); sv2.addWidget(b)

        sv2.addStretch()
        scroll.setWidget(sw); sv.addWidget(scroll)

        # Save / Reset
        ab = QFrame(); ab.setObjectName("SidebarActionsBar"); ab.setFixedHeight(96)
        ab_v = QVBoxLayout(ab); ab_v.setContentsMargins(16,12,16,12); ab_v.setSpacing(8)
        save_btn = QPushButton("✓   Save Settings"); save_btn.setObjectName("BtnPrimary"); save_btn.setFixedHeight(40)
        save_btn.setCursor(Qt.PointingHandCursor); save_btn.clicked.connect(self.saved)
        reset_btn = QPushButton("↺   Reset Defaults"); reset_btn.setObjectName("BtnSecondary"); reset_btn.setFixedHeight(36)
        reset_btn.setCursor(Qt.PointingHandCursor); reset_btn.clicked.connect(self.reset)
        ab_v.addWidget(save_btn); ab_v.addWidget(reset_btn)
        sv.addWidget(ab)
        root.addWidget(sidebar)

        # ── Main stacked panels ────────────────────────────────────────────
        self.stack = QStackedWidget()
        self.stack.addWidget(ConnectionSection())
        self.stack.addWidget(StreamingSection())
        self.stack.addWidget(StageSection())
        self.stack.addWidget(ShortcutsSection())
        self.stack.addWidget(AboutSection())
        root.addWidget(self.stack, stretch=1)

    def _nav(self, idx: int):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self._nav_btns):
            b.setObjectName("SettingsNavItemActive" if i==idx else "SettingsNavItem")
            b.style().unpolish(b); b.style().polish(b)
