"""
pages/scan_page.py
──────────────────
Tile-scan configuration + SVG grid preview.

Vyuhaa API hooks marked with # [VYUHAA API].
"""

from __future__ import annotations
import math
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QGridLayout
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QByteArray, QRectF
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
from PySide6.QtSvgWidgets import QSvgWidget
import theme
import Vyuhaa_api as ofa

class ScanWorker(QThread):
    finished = Signal()
    progress = Signal(int, int, bytes) # col, row, frame
    
    def __init__(self, config):
        super().__init__()
        self.config = config
        
    def run(self):
        try:
            ofa.start_scan(
                self.config, 
                tile_callback=lambda c, r, f: self.progress.emit(c, r, f),
                done_callback=lambda: self.finished.emit()
            )
        except Exception as e:
            print(f"Scan aborted or failed: {e}")


class Stepper(QFrame):
    """A +/− stepper widget that emits value_changed."""

    value_changed = Signal(int)

    def __init__(self, initial: int, minimum: int, maximum: int,
                 unit: str = "tiles", step: int = 1, parent=None):
        super().__init__(parent)
        self._value   = initial
        self._minimum = minimum
        self._maximum = maximum
        self._step    = step
        self.unit     = unit
        self.setStyleSheet(f"""
            QFrame {{
                background:{theme.SURFACE2};border:1px solid {theme.BORDER};
                border-radius:10px;
            }}
        """)
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        btn_style = f"""
            QPushButton {{
                background:{theme.SURFACE};border:none;border-radius:0;
                font-size:12px;color:{theme.TEXT_MID};min-width:22px;min-height:22px;
            }}
            QPushButton:hover {{ background:rgba(30,184,168,0.10);color:{theme.TEAL}; }}
            QPushButton:pressed {{ background:rgba(30,184,168,0.15); }}
        """
        minus = QPushButton("−")
        minus.setStyleSheet(btn_style)
        minus.clicked.connect(self._decrement)

        self.display = QLabel()
        self.display.setAlignment(Qt.AlignCenter)
        self._refresh_display()

        plus = QPushButton("+")
        plus.setStyleSheet(btn_style)
        plus.clicked.connect(self._increment)

        layout.addWidget(minus)
        layout.addWidget(self.display, stretch=1)
        layout.addWidget(plus)

    def _refresh_display(self):
        self.display.setText(
            f'<span style="font-family:JetBrains Mono,monospace;font-size:10px;'
            f'font-weight:500;color:{theme.WHITE};">{self._value}</span>'
            f'<br/>'
            f'<span style="font-size:6px;color:{theme.TEXT_DIM};">{self.unit}</span>'
        )
        self.display.setTextFormat(Qt.RichText)

    def _increment(self):
        if self._value + self._step <= self._maximum:
            self._value += self._step
            self._refresh_display()
            self.value_changed.emit(self._value)

    def _decrement(self):
        if self._value - self._step >= self._minimum:
            self._value -= self._step
            self._refresh_display()
            self.value_changed.emit(self._value)

    @property
    def value(self) -> int:
        return self._value


class ScanPage(QWidget):
    """
    Scan configuration page.
    Signal: scan_started(ScanConfig) — emitted when user clicks Start Scan.
    """

    scan_started   = Signal(object)   # ofa.ScanConfig
    scan_progress_broadcast = Signal(int, int)   # (done_tiles, total_tiles)
    scan_state_broadcast    = Signal(str)         # "scanning" | "done" | "cancelled"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._width_tiles  = 10
        self._height_tiles = 10
        self._overlap_pct  = 10
        self._save_mode    = "save_only"   # "save_only" | "both" | "stitched_only"
        self._use_autofocus = False
        self._active_worker = None
        self._scan_tiles_done = 0   # incremented each time a tile callback fires
        
        self._mosaic_pixmap = QPixmap(250, 250)
        self._mosaic_pixmap.fill(Qt.transparent)
        
        self._cam_poll_timer = QTimer(self)
        self._cam_poll_timer.setInterval(100)
        self._cam_poll_timer.timeout.connect(self._refresh_camera_feed)
        
        self._build()
        self._refresh_stats()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_preview_panel(), stretch=1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(140)
        sidebar.setStyleSheet(f"QFrame{{background:{theme.SURFACE};border-right:1px solid {theme.BORDER};}}")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(12, 16, 12, 12)
        cl.setSpacing(0)

        title = QLabel("Scan")
        title.setStyleSheet(f"""
            font-family:'Syne',sans-serif;font-size:10px;font-weight:700;
            color:{theme.WHITE};margin-bottom:6px;padding-bottom:6px;
            border-bottom:1px solid {theme.BORDER};
        """)
        cl.addWidget(title)

        def _field_label(text):
            l = QLabel(text.upper())
            l.setStyleSheet(f"font-size:7px;color:{theme.TEXT_DIM};font-weight:700;letter-spacing:0.05em;margin-bottom:3px;background:transparent;margin-top:8px;")
            return l

        cl.addWidget(_field_label("Width (tiles)"))
        self.width_stepper = Stepper(10, 1, 50)
        self.width_stepper.value_changed.connect(self._on_width_changed)
        cl.addWidget(self.width_stepper)

        cl.addWidget(_field_label("Height (tiles)"))
        self.height_stepper = Stepper(10, 1, 50)
        self.height_stepper.value_changed.connect(self._on_height_changed)
        cl.addWidget(self.height_stepper)

        cl.addWidget(_field_label("Overlap"))
        self.overlap_stepper = Stepper(10, 0, 50, unit="%", step=5)
        self.overlap_stepper.value_changed.connect(self._on_overlap_changed)
        cl.addWidget(self.overlap_stepper)

        cl.addWidget(_field_label("Save Mode"))
        self._save_mode_btns = {}
        save_mode_frame = QFrame()
        save_mode_frame.setStyleSheet("background:transparent;")
        sml = QVBoxLayout(save_mode_frame)
        sml.setContentsMargins(0, 0, 0, 0)
        sml.setSpacing(3)
        _modes = [
            ("save_only",      "Save Only"),
            ("both",           "Save + Stitch"),
            ("stitched_only",  "Stitch Only"),
        ]
        _btn_base = f"""
            QPushButton {{
                background:{theme.SURFACE2};border:1px solid {theme.BORDER};
                border-radius:6px;font-size:8px;color:{theme.TEXT_MID};
                padding:3px 6px;text-align:left;
            }}
            QPushButton:checked {{
                background:rgba(30,184,168,0.15);border:1px solid {theme.TEAL};
                color:{theme.TEAL};font-weight:700;
            }}
            QPushButton:hover {{ background:rgba(30,184,168,0.08); }}
        """
        for mode_key, mode_label in _modes:
            btn = QPushButton(mode_label)
            btn.setCheckable(True)
            btn.setChecked(mode_key == self._save_mode)
            btn.setStyleSheet(_btn_base)
            btn.clicked.connect(lambda checked, k=mode_key: self._on_save_mode_changed(k))
            sml.addWidget(btn)
            self._save_mode_btns[mode_key] = btn
        cl.addWidget(save_mode_frame)

        cl.addWidget(_field_label("Options"))
        self.af_btn = QPushButton("✨ Autofocus per Tile")
        self.af_btn.setCheckable(True)
        self.af_btn.setChecked(self._use_autofocus)
        self.af_btn.setStyleSheet(_btn_base)
        self.af_btn.clicked.connect(self._on_af_changed)
        cl.addWidget(self.af_btn)

        # Stats in sidebar
        cl.addSpacing(20)
        
        # Grid for stats to keep them compact
        stats_grid = QGridLayout()
        stats_grid.setSpacing(10)
        
        self.sidebar_stat_tiles = self._make_stat("100", "Tiles")
        self.sidebar_stat_time  = self._make_stat("~4m", "Est. Time")
        self.sidebar_stat_cov   = self._make_stat("2.5mm²", "Coverage")
        
        stats_grid.addWidget(self.sidebar_stat_tiles, 0, 0)
        stats_grid.addWidget(self.sidebar_stat_time, 0, 1)
        stats_grid.addWidget(self.sidebar_stat_cov, 1, 0, 1, 2) # Coverage spans bottom row
        
        cl.addLayout(stats_grid)

        cl.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        action_frame = QFrame()
        action_frame.setStyleSheet(f"background:{theme.SURFACE};border-top:1px solid {theme.BORDER};")
        af = QVBoxLayout(action_frame)
        af.setContentsMargins(20, 12, 20, 16)

        self.start_btn = QPushButton("▶  Start Scan")
        self.start_btn.setStyleSheet(theme.BTN_PRIMARY)
        self.start_btn.clicked.connect(self._start_scan)
        af.addWidget(self.start_btn)
        layout.addWidget(action_frame)

        return sidebar

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet("background:#050a0f;")
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignCenter)

        # Container for Mosaic + Grid
        self.preview_container = QWidget()
        self.preview_container.setFixedSize(250, 250)
        self.preview_container.setStyleSheet("background:rgba(10,15,20,0.5);")
        
        # Mosaic Preview Layer
        self.mosaic_view = QLabel(self.preview_container)
        self.mosaic_view.setFixedSize(250, 250)
        self.mosaic_view.setAlignment(Qt.AlignCenter)
        self.mosaic_view.setStyleSheet("background:transparent;")
        self.mosaic_view.setPixmap(self._mosaic_pixmap)
        
        # Grid / SVG Layer
        self.svg_widget = QSvgWidget(self.preview_container)
        self.svg_widget.setFixedSize(250, 250)
        self.svg_widget.setStyleSheet("background:transparent;")
        self.svg_widget.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.svg_widget.setAttribute(Qt.WA_TranslucentBackground)
        
        # Ensure correct stacking: mosaic behind grid
        self.mosaic_view.lower()
        self.svg_widget.raise_()
        
        layout.addWidget(self.preview_container)

        return panel

    def _make_stat(self, value: str, label: str) -> QFrame:
        box = QFrame()
        box.setStyleSheet(f"background:rgba(15,20,25,0.7);border:1px solid {theme.BORDER};border-radius:8px;")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 6, 6, 6)
        bl.setSpacing(8)

        # Value pill
        vp = QLabel(value)
        vp.setObjectName("statVal")
        vp.setAlignment(Qt.AlignCenter)
        vp.setStyleSheet(f"""
            QLabel {{
                font-family:'JetBrains Mono',monospace; font-size:10px; color:{theme.TEAL}; font-weight:600;
                background:rgba(30,184,168,0.05); border:1px solid rgba(30,184,168,0.25);
                border-radius:10px; padding:1px 6px;
            }}
        """)
        
        # Label pill
        lp = QLabel(label)
        lp.setAlignment(Qt.AlignCenter)
        lp.setStyleSheet(f"""
            QLabel {{
                font-size:7px; color:{theme.TEXT_DIM}; font-weight:700;
                background:rgba(255,255,255,0.03); border:1px solid {theme.BORDER};
                border-radius:8px; padding:1px 4px;
            }}
        """)

        bl.addWidget(vp)
        bl.addWidget(lp)
        return box

    # ── Stats & preview updates ────────────────────────────────────────────────

    def _on_width_changed(self, v: int):
        self._width_tiles = v
        self._refresh_stats()

    def _on_height_changed(self, v: int):
        self._height_tiles = v
        self._refresh_stats()

    def _on_overlap_changed(self, v: int):
        self._overlap_pct = v
        self._refresh_stats()

    def _on_save_mode_changed(self, mode: str):
        self._save_mode = mode
        for key, btn in self._save_mode_btns.items():
            btn.setChecked(key == mode)

    def _on_af_changed(self, checked: bool):
        self._use_autofocus = checked

    def _refresh_stats(self):
        w, h = self._width_tiles, self._height_tiles
        tiles = w * h
        est_m = max(1, round(tiles * 2.5 / 60))
        cov   = round(tiles * 0.025, 1)

        self.sidebar_stat_tiles.findChild(QLabel, "statVal").setText(str(tiles))
        self.sidebar_stat_time.findChild(QLabel, "statVal").setText(f"~{est_m}m")
        self.sidebar_stat_cov.findChild(QLabel, "statVal").setText(f"{cov}mm²")
        self._reset_mosaic_canvas()
        self._draw_scan_grid(w, h)

    def _draw_scan_grid(self, w: int, h: int):
        """Render tile grid as inline SVG."""
        size = 250
        tw = min(size / w, 30) - 1
        th = min(size / h, 30) - 1
        ox = (size - w * (tw + 1)) / 2
        oy = (size - h * (th + 1)) / 2
        rects = []
        for row in range(h):
            for col in range(w):
                px = ox + col * (tw + 1)
                py = oy + row * (th + 1)
                op = 0.18 + abs(math.sin(col * 7.3 + row * 3.1)) * 0.35
                rects.append(
                    f'<rect x="{px:.1f}" y="{py:.1f}" width="{tw:.1f}" height="{th:.1f}" '
                    f'rx="1.5" fill="rgba(30,184,168,{op:.2f})" '
                    f'stroke="rgba(30,184,168,0.35)" stroke-width="0.5"/>'
                )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="250" height="250" '
            f'viewBox="0 0 250 250" style="opacity:0.75">'
            + "".join(rects) + "</svg>"
        )
        self.svg_widget.load(QByteArray(svg.encode("utf-8")))

    def _reset_mosaic_canvas(self):
        """Reset the mosaic pixmap when grid size changes."""
        self._mosaic_pixmap = QPixmap(250, 250)
        self._mosaic_pixmap.fill(QColor(10, 15, 20))
        if hasattr(self, "mosaic_view"):
            self.mosaic_view.setPixmap(self._mosaic_pixmap)

    # ── Scan lifecycle ─────────────────────────────────────────────────────────

    def _start_scan(self):
        """Pre-scan setup and worker start."""
        self._reset_mosaic_canvas()
        self._scan_tiles_done = 0
        self.start_btn.setText("■  Stop Scan")
        self.start_btn.setStyleSheet(theme.BTN_DANGER)
        # Safely reconnect
        try: self.start_btn.clicked.disconnect()
        except: pass
        self.start_btn.clicked.connect(self._stop_scan)

        config = ofa.ScanConfig(
            width_tiles   = self._width_tiles,
            height_tiles  = self._height_tiles,
            overlap_pct   = self._overlap_pct,
            save_mode     = self._save_mode,
            use_autofocus = self._use_autofocus
        )

        self._active_worker = ScanWorker(config)
        self._active_worker.progress.connect(self._on_scan_progress)
        self._active_worker.finished.connect(self._on_scan_finished)
        self._active_worker.start()
        self.scan_started.emit(config)
        total = self._width_tiles * self._height_tiles
        self.scan_state_broadcast.emit("scanning")
        self.scan_progress_broadcast.emit(0, total)

    def _stop_scan(self):
        """Abort active scan."""
        ofa.abort_scan()
        worker = self._active_worker
        if worker:
            self._active_worker = None # Nullify before waiting to avoid re-entry issues
            worker.terminate()
            worker.wait()
        self.scan_state_broadcast.emit("cancelled")
        self._on_scan_finished()

    def _on_scan_finished(self):
        """Cleanup UI state after scan completion or abort."""
        self.start_btn.setText("▶  Start Scan")
        self.start_btn.setStyleSheet(theme.BTN_PRIMARY)
        try: self.start_btn.clicked.disconnect()
        except: pass
        self.start_btn.clicked.connect(self._start_scan)
        self._active_worker = None
        # Broadcast completion only if scan ran to the end (not cancelled — _stop_scan broadcasts that)
        total = self._width_tiles * self._height_tiles
        if self._scan_tiles_done >= total and total > 0:
            self.scan_state_broadcast.emit("done")
            self.scan_progress_broadcast.emit(total, total)

    def _on_scan_progress(self, col: int, row: int, frame: bytes):
        """Update UI or log progress during scan."""
        self._scan_tiles_done += 1
        total = self._width_tiles * self._height_tiles
        self.scan_progress_broadcast.emit(self._scan_tiles_done, total)
        print(f"UI: Scan Progress -> Tile [{col}, {row}] received ({len(frame)} bytes)")
        
        # Update Mosaic
        w_tiles, h_tiles = self._width_tiles, self._height_tiles
        tile_w = 250 / w_tiles
        tile_h = 250 / h_tiles
        
        img = QImage.fromData(frame)
        if not img.isNull():
            with QPainter(self._mosaic_pixmap) as painter:
                target_rect = QRectF(col * tile_w, row * tile_h, tile_w, tile_h)
                painter.drawImage(target_rect, img)
            self.mosaic_view.setPixmap(self._mosaic_pixmap)

    def _refresh_camera_feed(self):
        """Poll the camera array and convert it to a small overlay thumbnail or similar."""
        # For now, stay with the request of "big pic" mosaic.
        pass

    def on_page_activated(self):
        """Called by main window when this page becomes visible."""
        if ofa.get_connection_status():
            self._cam_poll_timer.start()

    def on_page_deactivated(self):
        """Called by main window when leaving this page."""
        self._cam_poll_timer.stop()