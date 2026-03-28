"""
pages/home_page.py
──────────────────
Home screen — 3×2 grid of navigation tiles.
Emits navigate(screen_id: str) signal when a tile is clicked.
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
import theme
import Vyuhaa_api as ofa


class HomeTile(QFrame):
    """A single clickable tile on the home screen."""

    clicked = Signal(str)

    def __init__(self, icon: str, label: str, screen_id: str,
                 featured: bool = False, badge: str = "", parent=None):
        super().__init__(parent)
        self.screen_id = screen_id
        self.setFixedSize(theme.TILE_W, theme.TILE_H)
        self.setCursor(Qt.PointingHandCursor)
        self._build(icon, label, badge, featured)
        self._apply_style(featured)

    def _build(self, icon: str, label: str, badge: str, featured: bool):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 6, 4, 6)
        layout.setSpacing(4)

        if badge:
            badge_cnt = QWidget()
            bl = QHBoxLayout(badge_cnt)
            bl.setContentsMargins(0,0,0,0)
            
            self.badge_label = QLabel(badge)
            self.badge_label.setObjectName("tileBadge")
            self.badge_label.setStyleSheet(f"""
                QLabel#tileBadge {{
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 7px; font-weight: 700;
                    letter-spacing: 0.04em;
                    padding: 1px 4px; border-radius: 4px;
                    background: rgba(239,68,68,0.12);
                    border: 1px solid rgba(239,68,68,0.3);
                    color: {theme.RED};
                }}
            """)
            bl.addWidget(self.badge_label)
            bl.addStretch()
            layout.addWidget(badge_cnt)
        else:
            layout.addSpacing(10) # Keep baseline consistent

        layout.addStretch()
        
        icon_box = QLabel(icon)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setFixedSize(28, 28)
        icon_box.setStyleSheet(f"""
            QLabel {{
                background-color: {'rgba(30,184,168,0.10)' if featured else theme.SURFACE2};
                border: 1px solid {'rgba(30,184,168,0.3)' if featured else theme.BORDER};
                border-radius: 6px;
                font-size: 14px;
            }}
        """)

        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"""
            QLabel {{
                font-family: 'Syne', 'Segoe UI', sans-serif;
                font-weight: 600; font-size: 8px;
                letter-spacing: 0.03em; color: {theme.TEXT_MID};
                background: transparent;
            }}
        """)

        layout.addWidget(icon_box, alignment=Qt.AlignCenter)
        layout.addWidget(lbl, alignment=Qt.AlignCenter)
        layout.addStretch()

    def _apply_style(self, featured: bool):
        base = f"background-color: {theme.SURFACE}; border-radius: 16px;"
        if featured:
            base = (
                "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "stop:0 rgba(30,184,168,0.07), stop:1 #161b22);"
                f"border: 1px solid rgba(30,184,168,0.25); border-radius: 16px;"
            )
        else:
            base += f"border: 1px solid {theme.BORDER};"
        self.setStyleSheet(f"HomeTile {{ {base} }}")

    def mousePressEvent(self, ev):
        self.clicked.emit(self.screen_id)
        super().mousePressEvent(ev)

    # ── Public API called by main window ─────────────────────────────────────
    def set_badge(self, text: str, state: str = "disconnected"):
        """state: 'disconnected' | 'connected' | 'relay'"""
        if not hasattr(self, "badge_label"):
            return
        colours = {
            "disconnected": (
                "rgba(239,68,68,0.12)", "rgba(239,68,68,0.3)", theme.RED
            ),
            "connected": (
                "rgba(34,197,94,0.12)", "rgba(34,197,94,0.3)", theme.GREEN
            ),
            "relay": (
                "rgba(30,184,168,0.10)", "rgba(30,184,168,0.3)", theme.TEAL
            ),
        }
        bg, border, colour = colours.get(state, colours["disconnected"])
        self.badge_label.setText(text)
        self.badge_label.setStyleSheet(f"""
            QLabel#tileBadge {{
                font-family: 'JetBrains Mono', monospace;
                font-size: 9px; font-weight: 700; letter-spacing: 0.06em;
                padding: 2px 8px; border-radius: 10px;
                background: {bg}; border: 1px solid {border}; color: {colour};
            }}
        """)


class HomePage(QWidget):
    """
    Home screen with 3×2 tile grid.
    Signal: navigate(screen_id) — request to switch to another screen.
    """

    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1) # Lighter top stretch

        grid = QGridLayout()
        grid.setSpacing(theme.TILE_GAP)

        tiles = [
            # (icon, label, screen_id, featured, badge)
            ("🔌", "Connect",     "connect", True,  "DISCONNECTED"),
            ("🕹",  "Manual Move", "manual",  False, ""),
            ("⬡",  "Scan",        "scan",    False, ""),
            ("⚙️", "Calibration", "cal",     False, ""),
            ("📂", "Files",       "files",   False, "0 FILES"),
            ("≡",  "Settings",    "settings",False, ""),
        ]

        self.connect_tile: HomeTile | None = None
        self.files_tile: HomeTile | None = None

        for i, (icon, label, sid, feat, badge) in enumerate(tiles):
            tile = HomeTile(icon, label, sid, feat, badge)
            tile.clicked.connect(self.navigate)
            grid.addWidget(tile, i // 3, i % 3)
            if sid == "connect":
                self.connect_tile = tile
            elif sid == "files":
                self.files_tile = tile

        outer.addLayout(grid)
        outer.addStretch(1) # Revert to balanced stretch for exact centering

    # ── Called by main window on connection state changes ─────────────────────
    def update_connect_badge(self, text: str, state: str):
        if self.connect_tile:
            self.connect_tile.set_badge(text, state)

    def update_files_badge(self, count: int):
        if self.files_tile:
            self.files_tile.set_badge(f"{count} FILES", "relay") # Use teal color

    def on_page_activated(self):
        """Refresh dynamic badges."""
        scans = ofa.get_scans_list()
        self.update_files_badge(len(scans))