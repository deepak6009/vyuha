"""
pages/settings_page.py
───────────────────────
App settings with toggle rows.

Settings are stored in memory; persist to QSettings as needed.
Vyuhaa API hooks marked with # [VYUHAA API].
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QScrollArea
)
from PySide6.QtCore import Qt, Signal
import theme
import Vyuhaa_api as ofa   # placeholder


class ToggleSwitch(QFrame):
    """Simple on/off toggle that emits toggled(bool)."""

    toggled = Signal(bool)

    def __init__(self, initial: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._on = initial
        self._refresh_style()

    def _refresh_style(self):
        knob_pos = "20px" if self._on else "2px"
        knob_col  = "#000" if self._on else theme.TEXT_DIM
        bg        = theme.TEAL if self._on else theme.SURFACE2
        border    = theme.TEAL if self._on else theme.BORDER
        self.setStyleSheet(f"""
            QFrame {{
                background:{bg};border:1px solid {border};
                border-radius:11px;
            }}
        """)
        # Knob via child label (CSS trick)
        for child in self.findChildren(QLabel):
            child.deleteLater()
        knob = QLabel(self)
        knob.setFixedSize(18, 18)
        knob.move(int(knob_pos.replace("px", "")), 2)
        knob.setStyleSheet(f"background:{knob_col};border-radius:9px;")
        knob.show()

    def mousePressEvent(self, ev):
        self._on = not self._on
        self._refresh_style()
        self.toggled.emit(self._on)
        super().mousePressEvent(ev)

    @property
    def is_on(self) -> bool:
        return self._on


class SettingRow(QFrame):
    """One row in the settings list."""

    def __init__(self, title: str, description: str,
                 initial: bool = False, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                border-bottom:1px solid {theme.BORDER};
                background:transparent;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)

        info = QVBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(f"font-weight:600;font-size:11px;color:{theme.TEXT};background:transparent;")
        d = QLabel(description)
        d.setStyleSheet(f"font-size:9px;color:{theme.TEXT_DIM};margin-top:2px;background:transparent;")
        info.addWidget(t)
        info.addWidget(d)

        self.toggle = ToggleSwitch(initial)
        layout.addLayout(info, stretch=1)
        layout.addWidget(self.toggle, alignment=Qt.AlignRight | Qt.AlignVCenter)


class SettingsPage(QWidget):
    """General preferences screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main(), stretch=1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(160)
        sidebar.setStyleSheet(f"QFrame{{background:{theme.SURFACE};border-right:1px solid {theme.BORDER};}}")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 24, 20, 16)

        title = QLabel("Settings")
        title.setStyleSheet(f"font-family:'Syne',sans-serif;font-size:14px;font-weight:700;color:{theme.WHITE};")
        sub = QLabel("General Preferences")
        sub.setStyleSheet(f"font-size:12px;color:{theme.TEXT_DIM};margin-top:6px;")
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addStretch()
        return sidebar

    def _build_main(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;")

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(24, 24, 24, 24)
        cl.setSpacing(0)
        cl.setAlignment(Qt.AlignTop)

        inner = QFrame()
        inner.setMaximumWidth(500)
        inner.setStyleSheet("background:transparent;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)

        # ── Settings rows ─────────────────────────────────────────────────────
        settings = [
            ("Hardware Acceleration",  "Use GPU for camera decoding and image processing",    False),
            ("Auto-Update UI Theme",   "Adapt interface colors to system appearance",         False),
            ("Haptic Feedback",        "Vibration on touchscreen actions",                    True),
            ("Sound Alerts",           "Audible cues on scan completion and errors",          True),
            ("Developer Mode",         "Show hardware debug logs and raw telemetry",          False),
            ("Auto-Reconnect",         "Re-connect to last known hardware on app start",      True),
        ]

        self.rows: dict[str, SettingRow] = {}
        for title, desc, default in settings:
            row = SettingRow(title, desc, default)
            row.toggle.toggled.connect(
                lambda state, t=title: self._on_setting_changed(t, state)
            )
            il.addWidget(row)
            self.rows[title] = row

        cl.addWidget(inner)
        scroll.setWidget(content)
        return scroll

    def _on_setting_changed(self, title: str, value: bool):
        """
        Persist and apply a setting change.

        [VYUHAA API] Some settings require API calls, e.g.:
            if title == "Hardware Acceleration":
                # toggle GPU decode in camera pipeline
                pass
            if title == "Developer Mode":
                # enable verbose logging
                pass

        To persist: use QSettings:
            from PySide6.QtCore import QSettings
            qs = QSettings("Vyuhaa", "MicroscopeApp")
            qs.setValue(title, value)
        """
        pass

    def get_setting(self, title: str) -> bool:
        """Return current value of a named setting."""
        row = self.rows.get(title)
        return row.toggle.is_on if row else False