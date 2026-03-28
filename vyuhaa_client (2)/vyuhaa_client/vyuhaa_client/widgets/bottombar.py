"""
Vyuhaa Remote Client — BottomBar
Context hints + Home button.
"""

from __future__ import annotations
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal
from styles import TEAL, BORDER, SURFACE, TEXT_DIM, TEXT_MID


CONTEXT_MAP = {
    "home":     "Select a module to get started.",
    "connect":  "Enter relay credentials, then press Connect.  ·  ↗ connect  ✕ disconnect",
    "live":     "W A S D  move stage  ·  +/− focus  ·  Space capture  ·  ↗ end session",
    "scan":     "Configure grid & pattern, then start whole-slide acquisition.",
    "files":    "Browse, export, or delete acquired slide images and recordings.",
    "settings": "Adjust relay, streaming, stage, shortcut, and about settings.",
    "about":    "Vyuhaa Remote Client — AI-powered cervical cancer screening system.",
}


class BottomBar(QFrame):
    home_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BottomBar")
        self.setFixedHeight(40)

        h = QHBoxLayout(self); h.setContentsMargins(16, 0, 16, 0); h.setSpacing(12)

        self.context_lbl = QLabel(CONTEXT_MAP["home"])
        self.context_lbl.setStyleSheet(
            f"font-size:11px; color:{TEXT_DIM}; background:transparent; "
            f"font-family:'JetBrains Mono',monospace;"
        )

        self.home_btn = QPushButton("Home")
        self.home_btn.setObjectName("HomeBtnDisabled")
        self.home_btn.setFixedHeight(26)
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.clicked.connect(self.home_clicked)
        self.home_btn.setVisible(False)

        h.addWidget(self.context_lbl, 1)
        h.addWidget(self.home_btn)

    def set_screen(self, screen: str):
        self.context_lbl.setText(CONTEXT_MAP.get(screen, ""))
        # Home button intentionally hidden globally.
        self.home_btn.setVisible(False)
