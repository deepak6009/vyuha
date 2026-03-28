"""
pages/files_page.py
───────────────────
Premium File Explorer UI based on high-fidelity design.
Features sidebar navigation, grid browser with badges, and detail panel.
"""

from __future__ import annotations
import os
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QScrollArea, QSpacerItem, QSizePolicy, QLineEdit,
    QProgressBar, QGridLayout, QDialog
)
from PySide6.QtCore import Qt, Signal, QSize, QRect
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QFont
import theme
import Vyuhaa_api as ofa

# ── Helper Components ─────────────────────────────────────────────────────────

class SidebarItem(QPushButton):
    """Navigation item in the sidebar."""
    def __init__(self, icon_text: str, label: str, count: Optional[int] = None, active: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(active)
        self.setFixedHeight(36)
        self._build(icon_text, label, count)
        self._set_style()

    def _build(self, icon_text, label, count):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        self.icon_lbl = QLabel(icon_text)
        self.icon_lbl.setStyleSheet("font-size: 14px; background: transparent;")
        
        self.name_lbl = QLabel(label)
        self.name_lbl.setStyleSheet("font-size: 12px; font-weight: 500; background: transparent;")
        
        layout.addWidget(self.icon_lbl)
        layout.addWidget(self.name_lbl, stretch=1)

        if count is not None:
            self.count_lbl = QLabel(str(count))
            self.count_lbl.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_DIM}; background: transparent;")
            layout.addWidget(self.count_lbl)

    def _set_style(self):
        active_bg = "rgba(30,184,168,0.12)"
        active_color = theme.TEAL
        
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 8px;
                text-align: left; color: {theme.TEXT_MID};
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.05); color: {theme.WHITE}; }}
            QPushButton:checked {{ background: {active_bg}; color: {active_color}; font-weight: 600; }}
        """)

class FileCard(QFrame):
    """Grid item representing a file/scan."""
    clicked = Signal(dict)

    def __init__(self, data: dict, active: bool = False, parent=None):
        super().__init__(parent)
        self.data = data
        self.setFixedSize(110, 130)
        self.setCursor(Qt.PointingHandCursor)
        self._build()
        self.set_active(active)

    def _build(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(4)

        # Header: Checkbox + Type Badge
        header = QHBoxLayout()
        self.check = QLabel("☐")
        self.check.setStyleSheet(f"color: {theme.BORDER}; font-size: 14px;")
        
        badge_text = self.data["type"].upper()
        self.badge = QLabel(badge_text)
        badge_color = theme.TEAL if badge_text == "MSI" else theme.TEXT_DIM
        self.badge.setStyleSheet(f"""
            font-size: 8px; font-weight: 800; color: {badge_color};
            background: rgba(30,184,168,0.05); border: 1px solid {badge_color}44;
            padding: 1px 4px; border-radius: 4px;
        """)
        
        header.addWidget(self.check)
        header.addStretch()
        header.addWidget(self.badge)
        self.layout.addLayout(header)

        # Main Icon
        icon_map = {"msi": "🔬", "cap": "📷", "cal": "⚙️", "zip": "📤"}
        self.main_icon = QLabel(icon_map.get(self.data["type"], "📄"))
        self.main_icon.setAlignment(Qt.AlignCenter)
        self.main_icon.setStyleSheet("font-size: 24px; margin: 2px 0; background: transparent;")
        self.layout.addWidget(self.main_icon)

        # Labels
        self.name_lbl = QLabel(self.data["name"])
        self.name_lbl.setWordWrap(True)
        self.name_lbl.setStyleSheet(f"font-size: 9px; font-weight: 600; color: {theme.WHITE}; background: transparent;")
        
        size_str = self._format_size(self.data["size_bytes"])
        self.meta_lbl = QLabel(f".{self.data['type']} · {size_str}")
        self.meta_lbl.setStyleSheet(f"font-size: 9px; color: {theme.TEXT_DIM}; background: transparent;")

        self.layout.addWidget(self.name_lbl)
        self.layout.addWidget(self.meta_lbl)
        self.layout.addStretch()

    def _format_size(self, b):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if b < 1024: return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    def set_active(self, active):
        border = theme.TEAL if active else theme.BORDER
        bg = "rgba(30,184,168,0.05)" if active else theme.SURFACE2
        self.setStyleSheet(f"""
            QFrame {{
                background: {bg}; border: 1px solid {border}; border-radius: 12px;
            }}
        """)
        self.check.setText("☑" if active else "☐")
        self.check.setStyleSheet(f"color: {theme.TEAL if active else theme.BORDER}; font-size: 14px;")

    def mousePressEvent(self, event):
        self.clicked.emit(self.data)
        super().mousePressEvent(event)

# ── Image Viewer Dialog ───────────────────────────────────────────────────────

class ImageViewerDialog(QDialog):
    """Simple gallery to view images inside a scan folder."""
    def __init__(self, scan_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Viewing: {scan_name}")
        self.setFixedSize(700, 420)
        self.setStyleSheet(f"background: {theme.BG}; border: 1px solid {theme.BORDER};")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        header = QHBoxLayout()
        title = QLabel(f"SCANNED TILES - {scan_name}")
        title.setStyleSheet(f"font-size: 12px; font-weight: 800; color: {theme.TEAL};")
        header.addWidget(title)
        header.addStretch()
        
        close_btn = QPushButton("✕ Close")
        close_btn.setStyleSheet(theme.BTN_DANGER)
        close_btn.clicked.connect(self.accept)
        header.addWidget(close_btn)
        layout.addLayout(header)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: #0d1117; border: none;")
        
        content = QWidget()
        self.grid = QGridLayout(content)
        self.grid.setSpacing(5)
        self.grid.setContentsMargins(5, 5, 5, 5)
        
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        self._load_images(scan_name)

    def _load_images(self, scan_name):
        scan_dir = os.path.join(ofa.SCANS_DIR, scan_name, "images")
        if not os.path.exists(scan_dir):
            # Fallback if images are in root
            scan_dir = os.path.join(ofa.SCANS_DIR, scan_name)
            
        if not os.path.exists(scan_dir):
            return

        files = [f for f in os.listdir(scan_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        files.sort()
        
        col_limit = 5
        for i, f in enumerate(files[:100]): # Limit to first 100 for performance
            path = os.path.join(scan_dir, f)
            lbl = QLabel()
            pix = QPixmap(path).scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl.setPixmap(pix)
            lbl.setStyleSheet(f"border: 1px solid {theme.BORDER};")
            self.grid.addWidget(lbl, i // col_limit, i % col_limit)

# ── Main Page ─────────────────────────────────────────────────────────────────

class FilesPage(QWidget):
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_data = None
        self._current_filter = "all"
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Sidebar
        root.addWidget(self._build_sidebar())
        
        # 2. Main Center Area
        center_container = QWidget()
        center_layout = QVBoxLayout(center_container)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        
        center_layout.addWidget(self._build_top_bar())
        
        # Grid Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: #0d1117; border: none;")
        
        self.grid_content = QWidget()
        self.grid_layout = QGridLayout(self.grid_content)
        self.grid_layout.setContentsMargins(24, 24, 24, 24)
        self.grid_layout.setSpacing(20)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        
        self.scroll.setWidget(self.grid_content)
        center_layout.addWidget(self.scroll)
        
        # Bottom Status
        center_layout.addWidget(self._build_bottom_status())
        
        root.addWidget(center_container, stretch=1)
        
        # 3. Right Detail Panel
        root.addWidget(self._build_detail_panel())

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(160)
        sidebar.setStyleSheet(f"background: {theme.BG}; border-right: 1px solid {theme.BORDER};")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 24, 16, 16)
        layout.setSpacing(4)

        title = QLabel("FILES")
        title.setStyleSheet(f"font-family: 'Syne'; font-size: 14px; font-weight: 800; color: {theme.WHITE}; margin-bottom: 20px;")
        layout.addWidget(title)

        def section_lbl(txt):
            lbl = QLabel(txt.upper())
            lbl.setStyleSheet(f"font-size: 9px; color: {theme.TEXT_DIM}; font-weight: 700; letter-spacing: 0.1em; margin: 16px 0 8px 0;")
            return lbl

        layout.addWidget(section_lbl("Workspace"))
        self.nav_all = SidebarItem("📁", "All Files", active=True)
        self.nav_all.clicked.connect(lambda: self._set_filter("all"))
        layout.addWidget(self.nav_all)
        
        self.nav_star = SidebarItem("⭐", "Starred")
        layout.addWidget(self.nav_star)
        
        self.nav_recent = SidebarItem("🕒", "Recent")
        layout.addWidget(self.nav_recent)

        layout.addWidget(section_lbl("Collections"))
        self.nav_scans = SidebarItem("🔬", "Scans")
        self.nav_scans.clicked.connect(lambda: self._set_filter("msi"))
        layout.addWidget(self.nav_scans)
        
        self.nav_caps = SidebarItem("📸", "Captures")
        self.nav_caps.clicked.connect(lambda: self._set_filter("cap"))
        layout.addWidget(self.nav_caps)
        
        self.nav_cal = SidebarItem("⚙️", "Calibration")
        self.nav_cal.clicked.connect(lambda: self._set_filter("cal"))
        layout.addWidget(self.nav_cal)
        
        self.nav_exp = SidebarItem("📤", "Exports")
        self.nav_exp.clicked.connect(lambda: self._set_filter("zip"))
        layout.addWidget(self.nav_exp)
        
        self.nav_trash = SidebarItem("🗑", "Trash")
        layout.addWidget(self.nav_trash)

        layout.addStretch()

        # Storage Bar
        storage_box = QFrame()
        sl = QVBoxLayout(storage_box)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        
        st_head = QHBoxLayout()
        st_head.addWidget(QLabel("Storage"))
        st_head.addStretch()
        self.percent_lbl = QLabel("63%")
        self.percent_lbl.setStyleSheet(f"color: {theme.TEAL}; font-weight: 700;")
        st_head.addWidget(self.percent_lbl)
        sl.addLayout(st_head)
        
        self.storage_bar = QProgressBar()
        self.storage_bar.setFixedHeight(4)
        self.storage_bar.setTextVisible(False)
        self.storage_bar.setStyleSheet(f"""
            QProgressBar {{ background: {theme.SURFACE2}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {theme.TEAL}; border-radius: 2px; }}
        """)
        sl.addWidget(self.storage_bar)
        
        self.storage_usage_lbl = QLabel("31.4 GB / 50 GB used")
        self.storage_usage_lbl.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_DIM};")
        sl.addWidget(self.storage_usage_lbl)
        
        layout.addWidget(storage_box)
        return sidebar

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(40)
        bar.setStyleSheet(f"background: {theme.BG}; border-bottom: 1px solid {theme.BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(16)

        breadcrumb = QLabel("Workspace  ›  <span style='color:white;'>All Files</span>")
        breadcrumb.setTextFormat(Qt.RichText)
        breadcrumb.setStyleSheet(f"font-size: 11px; color: {theme.TEXT_DIM};")
        layout.addWidget(breadcrumb)
        
        layout.addStretch()

        search = QLineEdit()
        search.setPlaceholderText("Search Files..")
        search.setFixedWidth(160)
        search.setStyleSheet(theme.INPUT_FIELD)
        layout.addWidget(search)

        # View Toggles
        btn_grp = QFrame()
        btn_grp.setStyleSheet("background: transparent; border: none;")
        bl = QHBoxLayout(btn_grp)
        bl.setContentsMargins(0,0,0,0)
        bl.setSpacing(4)
        
        grid_btn = QPushButton("▦")
        grid_btn.setFixedSize(32, 32)
        grid_btn.setStyleSheet(f"background: {theme.SURFACE2}; color: {theme.TEAL}; border: 1px solid {theme.BORDER}; border-radius: 6px;")
        
        list_btn = QPushButton("≡")
        list_btn.setFixedSize(32, 32)
        list_btn.setStyleSheet(f"background: transparent; color: {theme.TEXT_DIM}; border: none;")
        
        bl.addWidget(grid_btn)
        bl.addWidget(list_btn)
        layout.addWidget(btn_grp)

        return bar

    def _build_bottom_status(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(40)
        bar.setStyleSheet(f"background: {theme.BG}; border-top: 1px solid {theme.BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)

        self.status_lbl = QLabel("• 147 files  • 31.4 GB used")
        self.status_lbl.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_DIM};")
        layout.addWidget(self.status_lbl)
        
        layout.addSpacing(20)
        
        self.selection_lbl = QLabel("2 items selected")
        self.selection_lbl.setStyleSheet(f"font-size: 10px; color: {theme.TEAL}; font-weight: 700; background: rgba(30,184,168,0.08); padding: 4px 10px; border-radius: 6px;")
        self.selection_lbl.hide()
        layout.addWidget(self.selection_lbl)

        layout.addStretch()
        return bar

    def _build_detail_panel(self) -> QWidget:
        self.detail_panel = QFrame()
        self.detail_panel.setFixedWidth(140)
        self.detail_panel.setStyleSheet(f"background: #0d1117; border-left: 1px solid {theme.BORDER};")
        layout = QVBoxLayout(self.detail_panel)
        layout.setContentsMargins(20, 32, 20, 20)
        layout.setSpacing(20)

        # Preview Area
        self.det_preview = QFrame()
        self.det_preview.setFixedHeight(120)
        self.det_preview.setStyleSheet(f"background: {theme.SURFACE}; border-radius: 12px; border: 1px dashed {theme.BORDER};")
        pl = QVBoxLayout(self.det_preview)
        self.det_preview_icon = QLabel("📂")
        self.det_preview_icon.setAlignment(Qt.AlignCenter)
        self.det_preview_icon.setStyleSheet("font-size: 64px;")
        pl.addWidget(self.det_preview_icon)
        layout.addWidget(self.det_preview)

        # Info Header
        self.det_name = QLabel("Select a file")
        self.det_name.setWordWrap(True)
        self.det_name.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {theme.WHITE};")
        layout.addWidget(self.det_name)

        layout.addWidget(self._make_detail_row("FILE INFO"))
        
        # Grid for info
        info_grid = QWidget()
        igl = QGridLayout(info_grid)
        igl.setContentsMargins(0, 0, 0, 0)
        igl.setSpacing(10)
        
        igl.addWidget(QLabel("Size"), 0, 0)
        self.det_size = QLabel("-")
        self.det_size.setAlignment(Qt.AlignRight)
        self.det_size.setStyleSheet(f"color: {theme.TEXT_MID};")
        igl.addWidget(self.det_size, 0, 1)
        
        igl.addWidget(QLabel("Type"), 1, 0)
        self.det_type = QLabel("-")
        self.det_type.setAlignment(Qt.AlignRight)
        self.det_type.setStyleSheet(f"color: {theme.TEXT_MID};")
        igl.addWidget(self.det_type, 1, 1)
        
        layout.addWidget(info_grid)

        layout.addStretch()

        # Action Buttons
        self.btn_open = QPushButton("▶ Open in Viewer")
        self.btn_open.setStyleSheet(theme.BTN_PRIMARY)
        self.btn_open.clicked.connect(self._on_open_viewer)
        layout.addWidget(self.btn_open)

        self.btn_export = QPushButton("📤 Export")
        self.btn_export.setStyleSheet(theme.BTN_SECONDARY)
        layout.addWidget(self.btn_export)

        self.btn_copy = QPushButton("📄 Copy Path")
        self.btn_copy.setStyleSheet(theme.BTN_SECONDARY)
        layout.addWidget(self.btn_copy)

        self.btn_delete = QPushButton("🗑 Move to Trash")
        self.btn_delete.setStyleSheet(theme.BTN_DANGER)
        self.btn_delete.clicked.connect(self._on_delete_selected)
        layout.addWidget(self.btn_delete)

        return self.detail_panel

    def _make_detail_row(self, txt):
        l = QLabel(txt)
        l.setStyleSheet(f"font-size: 10px; color: {theme.TEXT_DIM}; font-weight: 700; letter-spacing: 0.1em; margin-top: 10px;")
        return l

    # ── Logic ──────────────────────────────────────────────────────────────────

    def on_page_activated(self):
        self._refresh_storage()
        self.refresh_list()

    def _refresh_storage(self):
        usage = ofa.get_disk_usage()
        # Convert to GB
        used_gb = usage["folder_size"] / (1024**3)
        total_gb = usage["total_capacity"] / (1024**3)
        
        self.percent_lbl.setText(f"{usage['percent_used']:.0f}%")
        self.storage_bar.setValue(int(usage['percent_used']))
        self.storage_usage_lbl.setText(f"{used_gb:.1f} GB / {total_gb:.0f} GB used")
        self.status_lbl.setText(f"• {used_gb:.1f} GB used in scans directory")

    def _set_filter(self, f):
        self._current_filter = f
        # Update sidebar UI
        for btn in [self.nav_all, self.nav_scans, self.nav_caps, self.nav_cal, self.nav_exp]:
            btn.setChecked(False)
        if f == "all": self.nav_all.setChecked(True)
        elif f == "msi": self.nav_scans.setChecked(True)
        elif f == "cap": self.nav_caps.setChecked(True)
        elif f == "cal": self.nav_cal.setChecked(True)
        elif f == "zip": self.nav_exp.setChecked(True)
        self.refresh_list()

    def refresh_list(self):
        # Clear grid
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        files = ofa.get_scans_list()
        # Filter
        if self._current_filter != "all":
            files = [f for f in files if f["type"] == self._current_filter]

        col_limit = 4
        for i, f in enumerate(files):
            card = FileCard(f, active=(self._selected_data and self._selected_data["id"] == f["id"]))
            card.clicked.connect(self._select_file)
            self.grid_layout.addWidget(card, i // col_limit, i % col_limit)
        
        self.status_lbl.setText(f"• {len(files)} items in this view")

    def _select_file(self, data):
        self._selected_data = data
        
        # Update grid selection UI
        for i in range(self.grid_layout.count()):
            w = self.grid_layout.itemAt(i).widget()
            if isinstance(w, FileCard):
                w.set_active(w.data["id"] == data["id"])

        # Update Detail Panel
        self.det_name.setText(data["name"])
        self.det_size.setText(self._format_size(data["size_bytes"]))
        self.det_type.setText(data["type"].upper())
        
        icon_map = {"msi": "🔬", "cap": "📷", "cal": "⚙️", "zip": "📤"}
        self.det_preview_icon.setText(icon_map.get(data["type"], "📂"))
        
        self.selection_lbl.show()
        self.selection_lbl.setText("1 item selected")

    def _format_size(self, b):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if b < 1024: return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    def _on_delete_selected(self):
        if self._selected_data:
            ofa.delete_scan(self._selected_data["name"])
            # Broadcast file list update to remote clients
            try:
                from remote_sharing import sharing_service
                if sharing_service is not None:
                    sharing_service.broadcast_state({"kind": "files_list_updated"})
            except Exception:
                pass
            self._selected_data = None
            self.on_page_activated()

    def _on_open_viewer(self):
        if not self._selected_data:
            return
        # Always use the built-in viewer — its guaranteed to work on all platforms
        dialog = ImageViewerDialog(self._selected_data["name"], self)
        dialog.exec()