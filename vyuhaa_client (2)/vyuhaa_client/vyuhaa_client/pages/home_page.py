"""
Vyuhaa Remote Client — Home Page
6-tile navigation grid matching vyuhaa-remote-client-v3.html
"""

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QGridLayout,
                                QPushButton, QLabel, QSizePolicy)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class TileButton(QPushButton):
    """A single home-screen navigation tile."""

    def __init__(self, icon: str, label: str, variant: str = "default",
                 badge: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName(
            "HomeTileBtnFeatured" if variant == "featured"
            else "HomeTileBtnLive" if variant == "live"
            else "HomeTileBtn"
        )
        self.setFixedSize(180, 180)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet(
            "font-size: 26px; background: transparent; border: none;"
        )

        name_lbl = QLabel(label)
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #94a3b8; "
            "background: transparent; border: none; letter-spacing: 1px;"
        )

        layout.addWidget(icon_lbl)
        layout.addWidget(name_lbl)

        if badge:
            badge_lbl = QLabel(badge)
            badge_lbl.setAlignment(Qt.AlignCenter)
            badge_lbl.setStyleSheet(
                "font-size: 9px; font-family: 'JetBrains Mono', monospace; "
                "font-weight: 700; letter-spacing: 2px; padding: 2px 8px; "
                "border-radius: 10px; background: rgba(34,197,94,0.12); "
                "border: 1px solid rgba(34,197,94,0.30); color: #22c55e; "
                "border: none;"
            )
            layout.addWidget(badge_lbl)


class HomePage(QWidget):
    """
    Home screen with 3×2 tile grid.
    Emits navigate(str) with the target screen name.
    """

    navigate = Signal(str)   # e.g. "connect", "live", "scan", "files", "settings"

    # Tile definitions: (icon, label, screen, variant)
    TILES = [
        ("↗",  "Connect",   "connect",  "featured"),
        ("🎥", "Live View", "live",     "live"),
        ("⬡",  "Scan",      "scan",     "default"),
        ("📁", "Files",     "files",    "default"),
        ("≡",  "Settings",  "settings", "default"),
        ("🔬", "About",     "about",    "default"),
    ]

    def __init__(self, app_state=None, parent=None):
        super().__init__(parent)
        self._app_state = app_state
        self._live_tile = None
        self._live_badge = None
        self._build_ui()
        
        # Listen to device state changes
        if self._app_state:
            self._app_state.device_state_changed.connect(self._update_live_badge)
            # Sync initial state
            self._update_live_badge(self._app_state.device_status)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        outer.setContentsMargins(0, 0, 0, 0)

        grid = QGridLayout()
        grid.setSpacing(16)

        for i, (icon, label, screen, variant) in enumerate(self.TILES):
            # Create tile without badge initially
            # We enforce "HomeTileBtn" by default for the 'live' variant if not live
            actual_variant = variant if variant != "live" else "default"
            tile = TileButton(icon, label, actual_variant, "")
            tile.clicked.connect(lambda _, s=screen: self.navigate.emit(s))
            
            # Store reference to Live View tile for dynamic badge updates
            if screen == "live":
                self._live_tile = tile
                # Add badge label that we can show/hide
                self._live_badge = QLabel("LIVE")
                self._live_badge.setAlignment(Qt.AlignCenter)
                self._live_badge.setStyleSheet(
                    "font-size: 9px; font-family: 'JetBrains Mono', monospace; "
                    "font-weight: 700; letter-spacing: 2px; padding: 2px 8px; "
                    "border-radius: 10px; background: rgba(34,197,94,0.12); "
                    "border: 1px solid rgba(34,197,94,0.30); color: #22c55e; "
                    "border: none;"
                )
                self._live_badge.hide()  # Hidden by default
                tile.layout().addWidget(self._live_badge)
            
            row, col = divmod(i, 3)
            grid.addWidget(tile, row, col)

        outer.addLayout(grid)
    
    def _update_live_badge(self, state: str):
        """Show LIVE badge and green border only when device is 'live'."""
        if self._live_tile:
            if state == "live":
                self._live_tile.setObjectName("HomeTileBtnLive")
            else:
                self._live_tile.setObjectName("HomeTileBtn")
            # Force stylesheet update
            self._live_tile.style().unpolish(self._live_tile)
            self._live_tile.style().polish(self._live_tile)

        if self._live_badge:
            if state == "live":
                self._live_badge.show()
            else:
                self._live_badge.hide()
