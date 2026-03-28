"""
Vyuhaa Remote Client — TopBar
Shows logo, app title, relay/device status pills, latency, and operator badge.
"""

from __future__ import annotations
import os
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QColor, QPainter, QBrush, QPixmap
from styles import TEAL, GREEN, RED, AMBER, BORDER, SURFACE, TEXT_DIM, TEXT_MID, WHITE


# ── Animated status dot ───────────────────────────────────────────────────────

class BlinkDot(QWidget):
    """Small circle that pulses when active."""

    def __init__(self, color: str = TEAL, blink: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._color  = color
        self._blink  = blink
        self._alpha  = 1.0
        self._timer  = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._tick)
        if blink:
            self._timer.start()

    def _tick(self):
        self._alpha = 0.3 if self._alpha > 0.5 else 1.0
        self.update()

    def set_state(self, color: str, blink: bool = False):
        self._color = color
        self._blink = blink
        self._alpha = 1.0
        if blink:
            self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self._color)
        c.setAlphaF(self._alpha)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(c))
        p.drawEllipse(0, 0, 8, 8)
        p.end()


# ── Status pill ───────────────────────────────────────────────────────────────

class StatusPill(QFrame):
    """Compact pill with animated dot + text label."""

    def __init__(self, dot_color: str = TEXT_DIM, label: str = "---",
                 blink: bool = False, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:20px; }}"
        )
        h = QHBoxLayout(self); h.setContentsMargins(8, 0, 10, 0); h.setSpacing(5)

        self._dot = BlinkDot(dot_color, blink)
        self._lbl = QLabel(label)
        self._lbl.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:600; "
            f"color:{TEXT_MID}; background:transparent;"
        )
        h.addWidget(self._dot); h.addWidget(self._lbl)
        self.setFixedHeight(28)

    def update_pill(self, color: str, label: str, blink: bool = False):
        self._dot.set_state(color, blink)
        self._lbl.setText(label)
        self._lbl.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:600; "
            f"color:{color}; background:transparent;"
        )


# ── TopBar ────────────────────────────────────────────────────────────────────

class TopBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(52)

        h = QHBoxLayout(self); h.setContentsMargins(20, 0, 20, 0); h.setSpacing(12)

        # Logo + title
        logo = QLabel()
        logo.setFixedSize(24, 24)
        logo.setStyleSheet("background:transparent;")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        client_dir = os.path.dirname(base_dir)
        workspace_root = os.path.abspath(os.path.join(client_dir, "..", ".."))
        logo_candidates = [
            os.path.join(workspace_root, "vyuhaa-logo.png"),
            os.path.join(workspace_root, "Vyuhaa-Logo.png"),
        ]
        for lp in logo_candidates:
            if os.path.exists(lp):
                pm = QPixmap(lp)
                if not pm.isNull():
                    logo.setPixmap(pm.scaled(22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                break

        title = QLabel(f"Argen <span style='font-size:11px; color:{TEXT_DIM};'>by vyuhaa</span>")
        title.setTextFormat(Qt.RichText)
        title.setObjectName("AppTitle")

        sep = QFrame(); sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color:{BORDER}; background:{BORDER}; border:none; max-width:1px;")
        sep.setFixedHeight(20)

        # Status pills
        self.relay_pill  = StatusPill(TEXT_DIM, "Relay: Offline", blink=False)
        self.device_pill = StatusPill(TEXT_DIM, "Device: Offline", blink=False)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet(f"color:{BORDER}; background:{BORDER}; border:none; max-width:1px;")
        sep2.setFixedHeight(20)

        self.latency_pill = StatusPill(TEXT_DIM, "--- ms", blink=False)

        # Operator badge
        self.op_badge = QLabel("OPR: Arun")
        self.op_badge.setStyleSheet(
            f"font-size:11px; font-family:'JetBrains Mono',monospace; font-weight:700; "
            f"color:{TEAL}; background:rgba(30,184,168,0.10); border:1px solid rgba(30,184,168,0.3); "
            f"border-radius:12px; padding:3px 10px;"
        )
        self.op_badge.setVisible(False)

        h.addWidget(logo)
        h.addWidget(title)
        h.addWidget(sep)
        h.addWidget(self.relay_pill)
        h.addWidget(self.device_pill)
        h.addWidget(sep2)
        h.addWidget(self.latency_pill)
        h.addStretch()
        h.addWidget(self.op_badge)

    # ── Public update methods ─────────────────────────────────────────────

    def update_relay(self, state: str, url: str = ""):
        """state: 'off' | 'connecting' | 'connected' | 'live' | 'error'"""
        state_map = {
            "off":        (TEXT_DIM, "Relay: Offline",     False),
            "connecting": (AMBER,    "Relay: Connecting…", True),
            "connected":  (TEAL,     "Relay: Connected",   False),
            "live":       (GREEN,    "Relay: Live",        False),
            "error":      (RED,      "Relay: Error",       False),
        }
        color, label, blink = state_map.get(state, state_map["off"])
        if url:
            host = url.replace("wss://","").replace("ws://","").split("/")[0]
            label = f"Relay: {host}" if state == "connected" else label
        self.relay_pill.update_pill(color, label, blink)

    def update_device(self, state: str):
        """state: 'offline' | 'searching' | 'found' | 'live'"""
        state_map = {
            "offline":   (TEXT_DIM, "Device: Offline",   False),
            "searching": (AMBER,    "Device: Searching…",True),
            "found":     (TEAL,     "Device: Found",     False),
            "live":      (GREEN,    "Device: Live",      False),
        }
        color, label, blink = state_map.get(state, state_map["offline"])
        self.device_pill.update_pill(color, label, blink)
        self.op_badge.setVisible(state == "live")

    def update_latency(self, ms: int):
        color = (GREEN if ms < 20 else AMBER if ms < 60 else RED)
        self.latency_pill.update_pill(color, f"{ms} ms")
