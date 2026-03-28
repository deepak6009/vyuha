"""
Vyuhaa Remote Client — Files Page
Two-panel: sidebar (search/filter) + main (file grid/list browser).
"""

from __future__ import annotations
import os
import datetime
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QSizePolicy, QScrollArea, QLineEdit,
    QComboBox, QGridLayout, QProgressBar, QFileDialog
)
from PySide6.QtCore import Qt, Signal
from styles import TEAL, GREEN, RED, AMBER, BORDER, SURFACE, SURFACE2, BG, TEXT_DIM, TEXT_MID, WHITE

TYPE_META = {
    "wsi":     {"label": "WSI",     "color": TEAL,  "icon": "⬡"},
    "scan":    {"label": "SCAN",    "color": TEAL,  "icon": "🔬"},
    "capture": {"label": "CAPTURE", "color": AMBER, "icon": "📷"},
    "video":   {"label": "VIDEO",   "color": GREEN, "icon": "🎥"},
}


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b //= 1024
    return f"{b:.1f} TB"


def _fmt_date(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%d %b %Y")
    except Exception:
        return ""


# ── File card (grid view) ─────────────────────────────────────────────────────

class FileCard(QFrame):
    download_requested = Signal(str)
    delete_requested   = Signal(str)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self._name = info["name"]
        meta = TYPE_META.get(info.get("type", "scan"), TYPE_META["scan"])
        self.setFixedSize(200, 170)
        self.setStyleSheet(
            f"QFrame {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px; }}"
            f"QFrame:hover {{ border-color: {meta['color']}; }}"
        )
        v = QVBoxLayout(self); v.setContentsMargins(14,12,14,12); v.setSpacing(6)

        # Icon + type badge row
        top = QHBoxLayout(); top.setSpacing(6)
        icon_lbl = QLabel(meta["icon"])
        icon_lbl.setStyleSheet("font-size:22px; background:transparent;")
        badge = QLabel(meta["label"])
        badge.setStyleSheet(
            f"font-size:9px; font-weight:700; letter-spacing:1px; padding:2px 8px;"
            f"border-radius:8px; background:transparent; color:{meta['color']};"
            f"border:1px solid {meta['color']}; font-family:'JetBrains Mono',monospace;"
        )
        top.addWidget(icon_lbl); top.addWidget(badge); top.addStretch()
        v.addLayout(top)

        # File name
        name_lbl = QLabel(info["name"])
        name_lbl.setWordWrap(True)
        name_lbl.setStyleSheet(
            "font-size:11px; font-weight:600; color:#e2e8f0; background:transparent; line-height:1.4;"
        )
        name_lbl.setFixedHeight(40)
        v.addWidget(name_lbl)

        # Meta row
        meta_row = QHBoxLayout(); meta_row.setSpacing(6)
        size_str = _fmt_size(info.get("size_bytes", 0)) if "size_bytes" in info else info.get("size", "—")
        date_str = _fmt_date(info["modified"]) if "modified" in info else info.get("date", "")
        size_lbl = QLabel(size_str)
        size_lbl.setStyleSheet(f"font-size:10px; color:{TEXT_DIM}; background:transparent;")
        date_lbl = QLabel(date_str)
        date_lbl.setStyleSheet(f"font-size:10px; color:{TEXT_DIM}; background:transparent;")
        meta_row.addWidget(size_lbl); meta_row.addStretch(); meta_row.addWidget(date_lbl)
        v.addLayout(meta_row)

        if info.get("tiles"):
            tile_lbl = QLabel(f"{info['tiles']:,} tiles")
            tile_lbl.setStyleSheet(
                f"font-size:9px; font-family:'JetBrains Mono',monospace; "
                f"color:{TEAL}; background:transparent; letter-spacing:1px;"
            )
            v.addWidget(tile_lbl)
        else:
            v.addStretch()

        # Action row
        act = QHBoxLayout(); act.setSpacing(6)
        dl_btn = QPushButton("↓ Download"); dl_btn.setObjectName("BtnSecondary")
        dl_btn.setFixedHeight(28); dl_btn.setCursor(Qt.PointingHandCursor)
        dl_btn.clicked.connect(lambda: self.download_requested.emit(self._name))
        del_btn = QPushButton("✕"); del_btn.setObjectName("BtnDanger")
        del_btn.setFixedSize(28, 28); del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._name))
        act.addWidget(dl_btn, 1); act.addWidget(del_btn)
        v.addLayout(act)


# ── File row (list view) ──────────────────────────────────────────────────────

class FileRow(QFrame):
    download_requested = Signal(str)
    delete_requested   = Signal(str)

    def __init__(self, info: dict, parent=None):
        super().__init__(parent)
        self._name = info["name"]
        meta = TYPE_META.get(info.get("type", "scan"), TYPE_META["scan"])
        self.setFixedHeight(52)
        self.setStyleSheet(
            f"QFrame {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:10px; }}"
            f"QFrame:hover {{ border-color: {meta['color']}; }}"
        )
        h = QHBoxLayout(self); h.setContentsMargins(14,0,14,0); h.setSpacing(12)

        icon_lbl = QLabel(meta["icon"])
        icon_lbl.setStyleSheet("font-size:18px; background:transparent;")

        badge = QLabel(meta["label"])
        badge.setFixedWidth(60)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"font-size:9px; font-weight:700; letter-spacing:1px; padding:2px 6px;"
            f"border-radius:6px; background:transparent; color:{meta['color']};"
            f"border:1px solid {meta['color']}; font-family:'JetBrains Mono',monospace;"
        )

        name_lbl = QLabel(info["name"])
        name_lbl.setStyleSheet("font-size:12px; font-weight:500; color:#e2e8f0; background:transparent;")
        name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        size_str = _fmt_size(info.get("size_bytes", 0)) if "size_bytes" in info else info.get("size", "—")
        date_str = _fmt_date(info["modified"]) if "modified" in info else info.get("date", "")
        size_lbl = QLabel(size_str)
        size_lbl.setStyleSheet(f"font-size:11px; color:{TEXT_DIM}; background:transparent; min-width:60px;")
        date_lbl = QLabel(date_str)
        date_lbl.setStyleSheet(f"font-size:11px; color:{TEXT_DIM}; background:transparent; min-width:90px;")

        dl_btn = QPushButton("↓"); dl_btn.setObjectName("BtnSecondary")
        dl_btn.setFixedSize(30, 30); dl_btn.setCursor(Qt.PointingHandCursor)
        dl_btn.clicked.connect(lambda: self.download_requested.emit(self._name))
        del_btn = QPushButton("✕"); del_btn.setObjectName("BtnDanger")
        del_btn.setFixedSize(30, 30); del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._name))

        h.addWidget(icon_lbl); h.addWidget(badge); h.addWidget(name_lbl)
        h.addWidget(size_lbl); h.addWidget(date_lbl)
        h.addWidget(dl_btn);   h.addWidget(del_btn)


# ── Files Page ────────────────────────────────────────────────────────────────

class FilesPage(QWidget):
    download_requested = Signal(str)   # scan name
    refresh_requested  = Signal()      # ask API client to call get_files()
    home_requested     = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view   = "grid"
        self._filter = "all"
        self._files: list[dict] = []
        self._filter_btns: dict[str, QPushButton] = {}
        self._view_btns:   dict[str, QPushButton] = {}
        self._build_ui()
        self._load_files()

    def _build_ui(self):
        root = QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────────
        sidebar = QFrame(); sidebar.setObjectName("SideBar"); sidebar.setFixedWidth(280)
        sv = QVBoxLayout(sidebar); sv.setContentsMargins(0,0,0,0); sv.setSpacing(0)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        sw = QWidget(); sv2 = QVBoxLayout(sw); sv2.setContentsMargins(20,24,20,16); sv2.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Files"); title.setObjectName("SidebarTitle")
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
        sv2.addSpacing(4)

        # Search
        search_lbl = QLabel("SEARCH"); search_lbl.setObjectName("FieldLabel"); sv2.addWidget(search_lbl)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search files…")
        self.search.textChanged.connect(self._load_files); sv2.addWidget(self.search)

        # Filter tabs
        filter_lbl = QLabel("FILTER BY TYPE"); filter_lbl.setObjectName("FieldLabel"); sv2.addWidget(filter_lbl)
        for fid, flbl in (("all","All"),("wsi","WSI"),("capture","Captures"),("video","Videos")):
            b = QPushButton(flbl)
            b.setObjectName("FilterTabActive" if fid=="all" else "FilterTab")
            b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _, f=fid, btn=b: self._set_filter(f, btn))
            self._filter_btns[fid] = b; sv2.addWidget(b)

        # Sort
        sort_lbl = QLabel("SORT BY"); sort_lbl.setObjectName("FieldLabel"); sv2.addWidget(sort_lbl)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Date (newest first)","Name (A–Z)","Size (largest first)"])
        self.sort_combo.currentIndexChanged.connect(self._load_files); sv2.addWidget(self.sort_combo)
        sv2.addSpacing(6)

        # File count
        self.count_lbl = QLabel("7 files")
        self.count_lbl.setStyleSheet(f"font-size:12px; color:{TEXT_DIM}; background:transparent;")
        sv2.addWidget(self.count_lbl)

        # Storage bar
        store_lbl = QLabel("STORAGE USED"); store_lbl.setObjectName("FieldLabel"); sv2.addWidget(store_lbl)
        store_bar = QProgressBar(); store_bar.setRange(0,100); store_bar.setValue(62)
        store_bar.setFixedHeight(5); store_bar.setTextVisible(False); sv2.addWidget(store_bar)
        store_detail = QLabel("124 GB of 200 GB used")
        store_detail.setStyleSheet(f"font-size:10px; color:{TEXT_DIM}; background:transparent;")
        sv2.addWidget(store_detail)
        sv2.addStretch()

        scroll.setWidget(sw); sv.addWidget(scroll)
        root.addWidget(sidebar)

        # ── Main panel ────────────────────────────────────────────────────
        main = QWidget(); main_v = QVBoxLayout(main); main_v.setContentsMargins(0,0,0,0); main_v.setSpacing(0)

        # Toolbar
        tb = QFrame(); tb.setObjectName("FilesToolbar"); tb.setFixedHeight(44)
        tb_h = QHBoxLayout(tb); tb_h.setContentsMargins(16,0,16,0); tb_h.setSpacing(8)
        sel_lbl = QLabel("FILES"); sel_lbl.setStyleSheet(
            f"font-size:11px; font-family:'JetBrains Mono',monospace; font-weight:700; "
            f"color:{TEXT_DIM}; background:transparent; letter-spacing:2px;"
        )
        exp_all = QPushButton("↓ Export Selected"); exp_all.setObjectName("BtnSecondary"); exp_all.setFixedHeight(30)
        exp_all.setCursor(Qt.PointingHandCursor)
        del_all = QPushButton("✕ Delete Selected"); del_all.setObjectName("BtnDanger"); del_all.setFixedHeight(30)
        del_all.setCursor(Qt.PointingHandCursor)

        vsep = QFrame(); vsep.setFrameShape(QFrame.VLine)
        vsep.setStyleSheet(f"color:{BORDER};background:{BORDER};border:none;max-width:1px;")

        for vid, vlbl in (("grid","⊞"),("list","≡")):
            b = QPushButton(vlbl)
            b.setObjectName("ViewBtnActive" if vid=="grid" else "ViewBtn")
            b.setFixedSize(28,28); b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _, v=vid, btn=b: self._set_view(v, btn))
            self._view_btns[vid] = b

        refresh_btn = QPushButton("↻ Refresh"); refresh_btn.setObjectName("BtnSecondary"); refresh_btn.setFixedHeight(30)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_requested.emit)

        tb_h.addWidget(sel_lbl); tb_h.addStretch()
        tb_h.addWidget(refresh_btn); tb_h.addWidget(exp_all); tb_h.addWidget(del_all); tb_h.addWidget(vsep)
        for b in self._view_btns.values(): tb_h.addWidget(b)
        main_v.addWidget(tb)

        # File area
        self.file_scroll = QScrollArea(); self.file_scroll.setWidgetResizable(True)
        self.file_scroll.setFrameShape(QFrame.NoFrame)
        self.file_content = QWidget()
        self.file_lay = QVBoxLayout(self.file_content); self.file_lay.setContentsMargins(16,16,16,16)
        self.file_scroll.setWidget(self.file_content)
        main_v.addWidget(self.file_scroll, stretch=1)
        root.addWidget(main, stretch=1)

    # ── Public API ─────────────────────────────────────────────────────────

    def load_from_server(self, files: list) -> None:
        """Called when AppState.files_list_received fires with real server data."""
        self._files = files
        self.count_lbl.setText(f"{len(files)} file{'s' if len(files) != 1 else ''}")
        self._load_files()

    def on_download_progress(self, name: str, done: int, total: int) -> None:
        """Update download progress label (wired externally if needed)."""
        pass  # Could show a progress bar overlay; kept simple for now

    # ── Logic ─────────────────────────────────────────────────────────────

    def _set_filter(self, fid: str, btn: QPushButton):
        self._filter = fid
        for f, b in self._filter_btns.items():
            b.setObjectName("FilterTabActive" if f==fid else "FilterTab")
            b.style().unpolish(b); b.style().polish(b)
        self._load_files()

    def _set_view(self, vid: str, btn: QPushButton):
        self._view = vid
        for v, b in self._view_btns.items():
            b.setObjectName("ViewBtnActive" if v==vid else "ViewBtn")
            b.style().unpolish(b); b.style().polish(b)
        self._load_files()

    def _load_files(self):
        # Clear
        while self.file_lay.count():
            item = self.file_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        query = self.search.text().lower()
        files = [f for f in self._files
                 if (self._filter == "all" or f["type"] == self._filter)
                 and (not query or query in f["name"].lower())]

        self.count_lbl.setText(f"{len(files)} file{'s' if len(files)!=1 else ''}")

        if self._view == "grid":
            grid = QGridLayout(); grid.setSpacing(12)
            for i, info in enumerate(files):
                card = FileCard(info)
                card.download_requested.connect(self._on_download_requested)
                grid.addWidget(card, i//3, i%3)
            wrapper = QWidget(); wrapper.setLayout(grid)
            self.file_lay.addWidget(wrapper)
        else:
            for info in files:
                row = FileRow(info)
                row.download_requested.connect(self._on_download_requested)
                self.file_lay.addWidget(row)

        self.file_lay.addStretch()

    def _on_download_requested(self, name: str) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save scan as", os.path.join(os.path.expanduser("~"), name + ".zip"),
            "All Files (*)"
        )
        if dest:
            self.download_requested.emit(name + "|" + dest)  # name|dest_path packed
