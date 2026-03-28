"""
Vyuhaa Remote Client — Scan Page
Two-panel: sidebar (scan config) + main (tile grid + progress).
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QSizePolicy, QScrollArea, QComboBox,
    QSpinBox, QSlider, QLineEdit, QGridLayout, QProgressBar
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QBrush, QPen
from styles import TEAL, GREEN, RED, AMBER, BORDER, SURFACE, SURFACE2, BG, TEXT_DIM, TEXT_MID, WHITE


# ── Tile widget ───────────────────────────────────────────────────────────────

class TileCell(QWidget):
    STATES = {
        "idle":    ("#1c2330", "#2a3441"),
        "pending": ("rgba(245,158,11,0.15)", "rgba(245,158,11,0.4)"),
        "done":    ("rgba(30,184,168,0.15)",  "rgba(30,184,168,0.4)"),
        "active":  ("rgba(34,197,94,0.20)",   "rgba(34,197,94,0.6)"),
        "error":   ("rgba(239,68,68,0.15)",   "rgba(239,68,68,0.4)"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self.setFixedSize(26, 26)
        self.update_state("idle")

    def update_state(self, state: str):
        self._state = state
        bg, border = self.STATES.get(state, self.STATES["idle"])
        self.setStyleSheet(
            f"background: {bg}; border: 1px solid {border}; border-radius: 4px;"
        )


# ── Tile grid ─────────────────────────────────────────────────────────────────

class TileGrid(QWidget):
    def __init__(self, cols: int = 8, rows: int = 6, parent=None):
        super().__init__(parent)
        self._cols  = cols
        self._rows  = rows
        self._cells: list[TileCell] = []
        self._layout = QGridLayout(self)
        self._layout.setSpacing(4)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._rebuild()

    def rebuild(self, cols: int, rows: int):
        self._cols = cols; self._rows = rows
        for c in self._cells:
            self._layout.removeWidget(c); c.deleteLater()
        self._cells.clear()
        self._rebuild()

    def _rebuild(self):
        for r in range(self._rows):
            for c in range(self._cols):
                cell = TileCell()
                self._cells.append(cell)
                self._layout.addWidget(cell, r, c)

    def set_progress(self, done: int, total: int):
        for i, cell in enumerate(self._cells):
            if i < done:
                cell.update_state("done")
            elif i == done:
                cell.update_state("active")
            else:
                cell.update_state("idle")

    def reset(self):
        for cell in self._cells:
            cell.update_state("idle")


# ── Scan Page ─────────────────────────────────────────────────────────────────

class ScanPage(QWidget):
    """
    Signals
    -------
    scan_start_requested(cols, rows, overlap, pattern, label, objective)
    scan_pause_requested()
    scan_resume_requested()
    scan_cancel_requested()
    """

    scan_start_requested  = Signal(int, int, int, str, str, str)
    scan_pause_requested  = Signal()
    scan_resume_requested = Signal()
    scan_cancel_requested = Signal()
    home_requested        = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanning  = False
        self._paused    = False
        self._done      = 0
        self._total     = 48
        self._pattern   = "raster"
        self._pattern_btns: dict[str, QPushButton] = {}
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────────
        sidebar = QFrame(); sidebar.setObjectName("SideBar"); sidebar.setFixedWidth(280)
        sv = QVBoxLayout(sidebar); sv.setContentsMargins(0,0,0,0); sv.setSpacing(0)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sw = QWidget(); sv2 = QVBoxLayout(sw); sv2.setContentsMargins(20,24,20,16); sv2.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Scan"); title.setObjectName("SidebarTitle")
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
        sep.setStyleSheet(f"background:{BORDER};max-height:1px;border:none;")
        sv2.addWidget(sep); sv2.addSpacing(4)

        sub = QLabel("Configure a whole-slide imaging scan. Set pattern, grid, and overlap, then start the automated acquisition.")
        sub.setObjectName("SidebarSub"); sub.setWordWrap(True); sv2.addWidget(sub); sv2.addSpacing(4)

        # Pattern selector
        pat_lbl = QLabel("SCAN PATTERN"); pat_lbl.setObjectName("FieldLabel"); sv2.addWidget(pat_lbl)
        pat_row = QHBoxLayout(); pat_row.setSpacing(6)
        for pid, plbl in (("raster","RASTER"),("snake","SNAKE"),("spiral","SPIRAL")):
            b = QPushButton(plbl)
            b.setObjectName("PatternBtnActive" if pid=="raster" else "PatternBtn")
            b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _, p=pid, btn=b: self._select_pattern(p, btn))
            self._pattern_btns[pid] = b
            pat_row.addWidget(b)
        sv2.addLayout(pat_row)

        # Grid size
        gs_lbl = QLabel("GRID SIZE (COLS × ROWS)"); gs_lbl.setObjectName("FieldLabel"); sv2.addWidget(gs_lbl)
        gs_row = QHBoxLayout(); gs_row.setSpacing(8)
        self.cols_spin = QSpinBox(); self.cols_spin.setRange(1,32); self.cols_spin.setValue(8)
        self.cols_spin.valueChanged.connect(self._update_stats)
        x_lbl = QLabel("×"); x_lbl.setStyleSheet(f"color:{TEXT_MID};background:transparent;")
        self.rows_spin = QSpinBox(); self.rows_spin.setRange(1,32); self.rows_spin.setValue(6)
        self.rows_spin.valueChanged.connect(self._update_stats)
        self.tile_count_lbl = QLabel("48 tiles")
        self.tile_count_lbl.setStyleSheet(
            f"font-size:11px; font-family:'JetBrains Mono',monospace; color:{TEXT_DIM}; background:transparent;"
        )
        gs_row.addWidget(self.cols_spin); gs_row.addWidget(x_lbl); gs_row.addWidget(self.rows_spin)
        gs_row.addWidget(self.tile_count_lbl); gs_row.addStretch()
        sv2.addLayout(gs_row)

        # Overlap
        ov_lbl = QLabel("OVERLAP"); ov_lbl.setObjectName("FieldLabel"); sv2.addWidget(ov_lbl)
        ov_row = QHBoxLayout(); ov_row.setSpacing(10)
        self.overlap_slider = QSlider(Qt.Horizontal)
        self.overlap_slider.setRange(0,40); self.overlap_slider.setValue(15)
        self.overlap_slider.valueChanged.connect(lambda v: (
            self.overlap_val.setText(f"{v}%"), self._update_stats()
        ))
        self.overlap_val = QLabel("15%")
        self.overlap_val.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace; font-size:12px; font-weight:600; "
            f"color:{TEAL}; background:transparent; min-width:36px;"
        )
        ov_row.addWidget(self.overlap_slider,1); ov_row.addWidget(self.overlap_val)
        sv2.addLayout(ov_row)

        # Label
        area_lbl = QLabel("AREA / SPECIMEN LABEL"); area_lbl.setObjectName("FieldLabel"); sv2.addWidget(area_lbl)
        self.scan_label = QLineEdit("Slide A — Zone 1"); sv2.addWidget(self.scan_label)

        # Objective
        obj_lbl = QLabel("OBJECTIVE LENS"); obj_lbl.setObjectName("FieldLabel"); sv2.addWidget(obj_lbl)
        self.objective = QComboBox()
        self.objective.addItems(["40× (FOV 220 μm)", "20× (FOV 440 μm)", "10× (FOV 880 μm)"])
        self.objective.currentIndexChanged.connect(self._update_stats)
        sv2.addWidget(self.objective)

        # Stats
        stats_grid = QGridLayout(); stats_grid.setSpacing(8)
        self.stat_tiles = self._make_stat("TOTAL TILES",    "48")
        self.stat_time  = self._make_stat("EST. TIME",      "3 m 12 s")
        self.stat_area  = self._make_stat("AREA COVERAGE",  "1.76 mm²")
        self.stat_size  = self._make_stat("FILE SIZE (EST.)","286 MB")
        for i, (lbl_w, val_w) in enumerate((
            self.stat_tiles, self.stat_time, self.stat_area, self.stat_size
        )):
            f = QFrame()
            f.setStyleSheet(f"background:{SURFACE2};border:1px solid {BORDER};border-radius:10px;")
            fv = QVBoxLayout(f); fv.setContentsMargins(10,8,10,8); fv.setSpacing(2)
            fv.addWidget(lbl_w); fv.addWidget(val_w)
            stats_grid.addWidget(f, i//2, i%2)
        sv2.addLayout(stats_grid)
        sv2.addStretch()

        scroll.setWidget(sw); sv.addWidget(scroll)

        # Sidebar buttons
        ab = QFrame(); ab.setObjectName("SidebarActionsBar"); ab.setFixedHeight(116)
        ab_v = QVBoxLayout(ab); ab_v.setContentsMargins(20,12,20,12); ab_v.setSpacing(8)
        self.start_btn  = QPushButton("⬡   Start Scan"); self.start_btn.setObjectName("BtnPrimary"); self.start_btn.setFixedHeight(44)
        self.start_btn.setCursor(Qt.PointingHandCursor); self.start_btn.clicked.connect(self._start_scan)
        self.pause_btn  = QPushButton("⏸   Pause"); self.pause_btn.setObjectName("BtnSecondary"); self.pause_btn.setFixedHeight(44)
        self.pause_btn.setCursor(Qt.PointingHandCursor); self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self._pause_scan)
        reset_btn = QPushButton("↺   Reset Grid"); reset_btn.setObjectName("BtnSecondary"); reset_btn.setFixedHeight(36)
        reset_btn.setCursor(Qt.PointingHandCursor); reset_btn.clicked.connect(self._reset_scan)
        ab_v.addWidget(self.start_btn); ab_v.addWidget(self.pause_btn); ab_v.addWidget(reset_btn)
        sv.addWidget(ab)
        root.addWidget(sidebar)

        # ── Main panel ────────────────────────────────────────────────────
        main = QWidget(); main_v = QVBoxLayout(main); main_v.setContentsMargins(0,0,0,0); main_v.setSpacing(0)

        # Banner
        self.banner = QFrame()
        self.banner.setStyleSheet(
            f"QFrame{{background:{SURFACE2};border:1px solid {BORDER};border-radius:10px;margin:20px 20px 0 20px;}}"
        )
        self.banner.setFixedHeight(68)
        ban_h = QHBoxLayout(self.banner); ban_h.setContentsMargins(16,0,16,0); ban_h.setSpacing(12)
        self.ban_icon  = QLabel("⬡"); self.ban_icon.setStyleSheet("font-size:18px;background:transparent;")
        ban_text = QVBoxLayout(); ban_text.setSpacing(2)
        self.ban_title = QLabel("Ready to Scan"); self.ban_title.setStyleSheet("font-size:13px;font-weight:700;color:#fff;background:transparent;")
        self.ban_desc  = QLabel("Configure scan parameters and press Start to begin whole-slide acquisition.")
        self.ban_desc.setStyleSheet(f"font-size:11px;color:{TEXT_DIM};background:transparent;")
        ban_text.addWidget(self.ban_title); ban_text.addWidget(self.ban_desc)
        self.ban_badge = QLabel("IDLE")
        self.ban_badge.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;letter-spacing:1px;"
            f"padding:3px 10px;border-radius:8px;background:{SURFACE};color:{TEXT_DIM};border:1px solid {BORDER};"
        )
        ban_h.addWidget(self.ban_icon); ban_h.addLayout(ban_text); ban_h.addStretch(); ban_h.addWidget(self.ban_badge)
        main_v.addWidget(self.banner)

        # Tile grid area
        tile_area = QScrollArea(); tile_area.setWidgetResizable(True)
        tile_area.setFrameShape(QFrame.NoFrame)
        tile_w = QWidget(); tile_lay = QVBoxLayout(tile_w)
        tile_lay.setAlignment(Qt.AlignCenter); tile_lay.setContentsMargins(20,20,20,20)
        self.tile_grid = TileGrid(8, 6); tile_lay.addWidget(self.tile_grid, alignment=Qt.AlignCenter)
        tile_area.setWidget(tile_w); main_v.addWidget(tile_area, stretch=1)

        # Progress strip
        prog = QFrame(); prog.setObjectName("ScanProgressStrip"); prog.setFixedHeight(40)
        prog_h = QHBoxLayout(prog); prog_h.setContentsMargins(20,0,20,0); prog_h.setSpacing(12)
        prog_lbl = QLabel("PROGRESS")
        prog_lbl.setStyleSheet(
            f"font-size:11px;font-family:'JetBrains Mono',monospace;color:{TEXT_DIM};background:transparent;"
        )
        self.prog_bar = QProgressBar(); self.prog_bar.setRange(0,100); self.prog_bar.setValue(0); self.prog_bar.setFixedHeight(5)
        self.prog_text = QLabel("0 / 48 tiles")
        self.prog_text.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;color:{TEAL};background:transparent;min-width:100px;"
        )
        prog_h.addWidget(prog_lbl); prog_h.addWidget(self.prog_bar,1); prog_h.addWidget(self.prog_text)
        main_v.addWidget(prog)
        root.addWidget(main, stretch=1)

    def _make_stat(self, label: str, value: str):
        lbl = QLabel(label); lbl.setObjectName("StatLabel"); lbl.setAlignment(Qt.AlignCenter)
        val = QLabel(value); val.setObjectName("StatValue"); val.setAlignment(Qt.AlignCenter)
        return lbl, val

    # ── Scan control ──────────────────────────────────────────────────────

    def _select_pattern(self, pid: str, btn: QPushButton):
        self._pattern = pid
        for p, b in self._pattern_btns.items():
            b.setObjectName("PatternBtnActive" if p==pid else "PatternBtn")
            b.style().unpolish(b); b.style().polish(b)

    def _update_stats(self):
        cols = self.cols_spin.value(); rows = self.rows_spin.value()
        total = cols * rows
        ov    = self.overlap_slider.value()
        obj   = self.objective.currentText()
        fov   = 220 if "40×" in obj else (440 if "20×" in obj else 880)
        tile_um = fov * (1 - ov/100)
        area  = (cols * tile_um / 1000) * (rows * tile_um / 1000)
        t_sec = total * 4
        size_mb = total * 6

        self.tile_count_lbl.setText(f"{total} tiles")
        self.stat_tiles[1].setText(str(total))
        self.stat_time[1].setText(f"{t_sec//60} m {t_sec%60:02d} s")
        self.stat_area[1].setText(f"{area:.2f} mm²")
        self.stat_size[1].setText(f"{size_mb} MB")
        self._total = total
        self.tile_grid.rebuild(cols, rows)
        self.prog_bar.setMaximum(total)
        self.prog_text.setText(f"0 / {total} tiles")

    def _start_scan(self):
        self._scanning = True; self._paused = False; self._done = 0
        cols = self.cols_spin.value(); rows = self.rows_spin.value()
        self.scan_start_requested.emit(
            cols, rows,
            self.overlap_slider.value(),
            self._pattern,
            self.scan_label.text(),
            self.objective.currentText()
        )
        self._set_banner("scanning", f"Scanning {cols}×{rows} grid…",
                         "Whole-slide acquisition in progress.", "SCANNING")
        self.start_btn.setVisible(False); self.pause_btn.setVisible(True)

    def _pause_scan(self):
        if not self._paused:
            self._paused = True; self.pause_btn.setText("▶   Resume")
            self.scan_pause_requested.emit()
            self._set_banner("paused","Scan Paused","Press Resume to continue.","PAUSED")
        else:
            self._paused = False; self.pause_btn.setText("⏸   Pause")
            self.scan_resume_requested.emit()
            self._set_banner("scanning","Scan Resumed","Acquisition continuing…","SCANNING")

    def _reset_scan(self):
        self._scanning = False; self._paused = False; self._done = 0
        self.scan_cancel_requested.emit()
        self.tile_grid.reset()
        self.prog_bar.setValue(0); self.prog_text.setText(f"0 / {self._total} tiles")
        self._set_banner("idle","Ready to Scan",
                         "Configure scan parameters and press Start to begin whole-slide acquisition.","IDLE")
        self.start_btn.setVisible(True); self.pause_btn.setVisible(False)
        self.pause_btn.setText("⏸   Pause")

    def _set_banner(self, mode: str, title: str, desc: str, badge: str):
        bg_map = {
            "idle":     (SURFACE2, BORDER, TEXT_DIM),
            "scanning": ("rgba(30,184,168,0.08)", "rgba(30,184,168,0.3)", TEAL),
            "paused":   ("rgba(245,158,11,0.08)", "rgba(245,158,11,0.3)", AMBER),
            "done":     ("rgba(34,197,94,0.08)",  "rgba(34,197,94,0.3)",  GREEN),
        }
        bg, border, badge_c = bg_map.get(mode, bg_map["idle"])
        self.banner.setStyleSheet(
            f"QFrame{{background:{bg};border:1px solid {border};border-radius:10px;margin:20px 20px 0 20px;}}"
        )
        self.ban_title.setText(title); self.ban_desc.setText(desc)
        self.ban_badge.setText(badge)
        self.ban_badge.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;letter-spacing:1px;"
            f"padding:3px 10px;border-radius:8px;background:transparent;color:{badge_c};border:1px solid {border};"
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def update_progress(self, done: int, total: int):
        self._done = done; self._total = total
        self.tile_grid.set_progress(done, total)
        self.prog_bar.setMaximum(total); self.prog_bar.setValue(done)
        self.prog_text.setText(f"{done} / {total} tiles")
        if done >= total and total > 0:
            self._set_banner("done","Scan Complete",f"{total} tiles acquired successfully.","DONE")
            self.start_btn.setVisible(True); self.pause_btn.setVisible(False)
