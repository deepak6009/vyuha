"""
pages/calibration_page.py
──────────────────────────
Calibration screen with three tabs: Autofocus, White Balance, Flatfield.

Vyuhaa API hooks marked with # [VYUHAA API].
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QColor, QPainter, QConicalGradient, QPen, QFont
import math
import theme
import Vyuhaa_api as ofa

class AutofocusWorker(QThread):
    finished = Signal(float)
    
    def run(self):
        score = ofa.run_autofocus()
        self.finished.emit(score)


class FocusRingWidget(QWidget):
    """Circular arc that shows focus score (0–600+)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(110, 110)
        self._score = 0.0
        self._quality = "—"

    def set_score(self, score: float):
        self._score = score
        self._quality = "GOOD" if score > 300 else "FAIR" if score > 100 else "POOR"
        self.update()

    def paintEvent(self, ev):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.Antialiasing)
            cx, cy, r = 55, 55, 45
            margin = 5

            # Background ring
            pen = QPen(QColor(theme.SURFACE2), 12, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, -360 * 16)

            # Filled arc
            pct = min(self._score / 600.0, 1.0)
            span = -int(pct * 360 * 16)
            pen2 = QPen(QColor(theme.TEAL), 12, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen2)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 90 * 16, span)

            # Score text
            p.setPen(QColor(theme.TEAL))
            font = p.font()
            font.setFamily("JetBrains Mono")
            font.setWeight(QFont.Weight.Medium)
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter, f"{self._score:.0f}")

            # Quality text below score
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Bold)
            p.setFont(font)
            p.setPen(QColor(theme.GREEN))
            rect = self.rect()
            rect.setTop(rect.center().y() + 8)
            p.drawText(rect, Qt.AlignHCenter | Qt.AlignTop, self._quality)



class CalibrationPage(QWidget):
    """
    Calibration screen with Autofocus / White Balance / Flatfield tabs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # print("DEBUG: CalibrationPage v2.1 (self.cap_btn fix) initialized")
        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_main_panel(), stretch=1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(140)
        sidebar.setStyleSheet(f"QFrame{{background:{theme.SURFACE};border-right:1px solid {theme.BORDER};}}")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(0)

        title = QLabel("Calibration")
        title.setStyleSheet(f"font-family:'Syne',sans-serif;font-size:11px;font-weight:700;color:{theme.WHITE};margin-bottom:12px;")
        layout.addWidget(title)

        # Tab buttons
        tabs_frame = QFrame()
        tabs_frame.setStyleSheet(f"""
            QFrame {{
                background:{theme.SURFACE2};border:1px solid {theme.BORDER};
                border-radius:10px; padding:4px;
            }}
        """)
        tabs_layout = QHBoxLayout(tabs_frame)
        tabs_layout.setContentsMargins(4, 4, 4, 4)
        tabs_layout.setSpacing(0)

        self.tab_buttons: list[QPushButton] = []
        tabs = [("Focus", "autofocus"), ("WB", "wb"), ("Flat", "ff")]
        for i, (label, tab_id) in enumerate(tabs):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setProperty("tabId", tab_id)
            btn.setStyleSheet(self._tab_style(i == 0))
            btn.clicked.connect(lambda _, tid=tab_id, b=btn: self._switch_tab(tid, b))
            tabs_layout.addWidget(btn)
            self.tab_buttons.append(btn)

        layout.addWidget(tabs_frame)
        layout.addStretch()
        return sidebar

    def _build_main_panel(self) -> QWidget:
        self.stack = QFrame()
        self.stack.setStyleSheet("background:transparent;")
        sl = QVBoxLayout(self.stack)
        sl.setContentsMargins(10, 10, 10, 10)

        self.autofocus_panel = self._build_autofocus_panel()
        self.wb_panel        = self._build_wb_panel()
        self.ff_panel        = self._build_ff_panel()

        for w in (self.autofocus_panel, self.wb_panel, self.ff_panel):
            sl.addWidget(w)

        self.wb_panel.hide()
        self.ff_panel.hide()
        return self.stack

    def _on_run_autofocus(self):
        self.af_run_btn.setEnabled(False)
        self.af_run_btn.setText("Running…")
        self.af_progress_lbl.setText("Scanning Z axis...")

        self._af_worker = AutofocusWorker()
        self._af_worker.finished.connect(self._on_autofocus_done)
        self._af_worker.start()

    def _on_autofocus_done(self, score: float):
        self.focus_ring.set_score(score)
        self.af_run_btn.setEnabled(True)
        self.af_run_btn.setText("Run Full Sweep")

    # ── Autofocus panel ───────────────────────────────────────────────────────

    def _build_autofocus_panel(self) -> QWidget:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        self.focus_ring = FocusRingWidget()
        self.focus_ring.set_score(442)
        layout.addWidget(self.focus_ring, alignment=Qt.AlignCenter)

        desc = QLabel("Contrast-based focus score. Values above 300 indicate good focus quality.")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet(f"color:{theme.TEXT_DIM};font-size:8px;max-width:260px;line-height:1.4;background:transparent;")
        layout.addWidget(desc, alignment=Qt.AlignCenter)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(10)

        self.af_run_btn = QPushButton("⟳  Autofocus")
        self.af_run_btn.setStyleSheet(theme.BTN_PRIMARY)
        self.af_run_btn.setFixedWidth(160)
        self.af_run_btn.clicked.connect(self._run_autofocus)

        save_btn = QPushButton("Save Focus")
        save_btn.setStyleSheet(theme.BTN_SECONDARY)
        save_btn.setFixedWidth(160)
        save_btn.clicked.connect(self._save_focus)

        btn_col.addWidget(self.af_run_btn)
        btn_col.addWidget(save_btn)
        layout.addLayout(btn_col)
        return panel

    def _run_autofocus(self):
        """
        Run the full autofocus sweep.
        """
        self.af_run_btn.setEnabled(False)
        self.af_run_btn.setText("Running...")
        
        self._af_worker = AutofocusWorker()
        self._af_worker.finished.connect(self._on_autofocus_done)
        self._af_worker.start()

    def _save_focus(self):
        """
        [VYUHAA API] Store current Z position as the reference focus.
        E.g.:  ofa.zero_stage() or save to config file.
        """
        pass

    # ── White Balance panel ───────────────────────────────────────────────────

    def _build_wb_panel(self) -> QWidget:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        rgb_row = QHBoxLayout()
        rgb_row.setSpacing(16)
        rgb_row.setAlignment(Qt.AlignCenter)

        self.wb_labels: dict[str, QLabel] = {}
        channels = [("R", "#f87171", 1.24), ("G", "#4ade80", 1.00), ("B", "#60a5fa", 0.91)]
        for ch, colour, val in channels:
            col = QVBoxLayout()
            col.setAlignment(Qt.AlignCenter)
            title = QLabel(ch)
            title.setAlignment(Qt.AlignCenter)
            title.setStyleSheet(f"font-size:9px;color:{theme.TEXT_DIM};margin-bottom:4px;background:transparent;")
            val_lbl = QLabel(f"{val:.2f}")
            val_lbl.setAlignment(Qt.AlignCenter)
            val_lbl.setStyleSheet(f"font-family:'JetBrains Mono',monospace;font-size:18px;color:{colour};font-weight:500;background:transparent;")
            col.addWidget(title)
            col.addWidget(val_lbl)
            self.wb_labels[ch] = val_lbl
            rgb_row.addLayout(col)

        layout.addLayout(rgb_row)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(10)
        awb_btn = QPushButton("⟳  Auto White Balance")
        awb_btn.setStyleSheet(theme.BTN_PRIMARY)
        awb_btn.setFixedWidth(220)
        awb_btn.clicked.connect(self._run_awb)

        rst_btn = QPushButton("Reset to Default")
        rst_btn.setStyleSheet(theme.BTN_SECONDARY)
        rst_btn.setFixedWidth(220)
        rst_btn.clicked.connect(self._reset_wb)

        btn_col.addWidget(awb_btn)
        btn_col.addWidget(rst_btn)
        layout.addLayout(btn_col)
        return panel

    def _run_awb(self):
        """
        [VYUHAA API] Replace with:
            r, g, b = ofa.run_white_balance()
            self.wb_labels['R'].setText(f'{r:.2f}')
            self.wb_labels['G'].setText(f'{g:.2f}')
            self.wb_labels['B'].setText(f'{b:.2f}')
        """
        pass

    def _reset_wb(self):
        """[VYUHAA API] Reset camera WB gains to 1.0, 1.0, 1.0."""
        for ch, val in [("R", "1.00"), ("G", "1.00"), ("B", "1.00")]:
            self.wb_labels[ch].setText(val)

    # ── Flatfield panel ───────────────────────────────────────────────────────

    def _build_ff_panel(self) -> QWidget:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        circle = QLabel("△")
        circle.setFixedSize(80, 80)
        circle.setAlignment(Qt.AlignCenter)
        circle.setStyleSheet(f"""
            font-size:24px;
            background:qradialgradient(cx:0.5,cy:0.5,radius:0.5,
                stop:0 rgba(30,184,168,0.25),stop:1 transparent);
            border:2px dashed rgba(30,184,168,0.3);
            border-radius:40px;
        """)
        layout.addWidget(circle, alignment=Qt.AlignCenter)

        desc = QLabel("Capture a blank slide to compute the flatfield correction for uniform illumination.")
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet(f"color:{theme.TEXT_DIM};font-size:10px;max-width:260px;line-height:1.4;background:transparent;")
        layout.addWidget(desc, alignment=Qt.AlignCenter)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(10)
        self.cap_btn = QPushButton("📷  Capture Flatfield")
        self.cap_btn.setStyleSheet(theme.BTN_PRIMARY)
        self.cap_btn.setFixedWidth(160)
        self.cap_btn.clicked.connect(self._capture_flatfield)

        clr_btn = QPushButton("Clear Correction")
        clr_btn.setStyleSheet(theme.BTN_SECONDARY)
        clr_btn.setFixedWidth(220)
        clr_btn.clicked.connect(self._clear_flatfield)

        btn_col.addWidget(self.cap_btn)
        btn_col.addWidget(clr_btn)
        layout.addLayout(btn_col)
        return panel

    def _capture_flatfield(self):
        """
        [VYUHAA API] Capture a blank slide to compute the flatfield correction.
        """
        self.cap_btn.setEnabled(False)
        self.cap_btn.setText("⏳ Capturing...")
        
        # We use a single-shot timer to allow the UI to refresh before the blocking call
        # Or better, just run it and update after.
        success = ofa.capture_flatfield()
        if success:
            self.cap_btn.setText("✅ Success!")
            QTimer.singleShot(3000, lambda: self.cap_btn.setText("📷 Capture Flatfield"))
        else:
            self.cap_btn.setText("❌ Failed")
            QTimer.singleShot(3000, lambda: self.cap_btn.setText("📷 Capture Flatfield"))
        
        self.cap_btn.setEnabled(True)

    def _clear_flatfield(self):
        """[VYUHAA API] call ofa.clear_flatfield()"""
        ofa.clear_flatfield()

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _tab_style(self, active: bool) -> str:
        if active:
            return f"QPushButton{{background:{theme.TEAL};color:#000;border-radius:7px;padding:4px 2px;font-size:9px;font-weight:600;border:none;}}"
        return f"""
            QPushButton {{background:transparent;color:{theme.TEXT_DIM};border:none;
                border-radius:7px;padding:4px 2px;font-size:9px;font-weight:600;}}
            QPushButton:hover {{color:{theme.TEXT};}}
        """

    def _switch_tab(self, tab_id: str, active_btn: QPushButton):
        for b in self.tab_buttons:
            b.setStyleSheet(self._tab_style(b is active_btn))
        mapping = {
            "autofocus": self.autofocus_panel,
            "wb":        self.wb_panel,
            "ff":        self.ff_panel,
        }
        for tid, w in mapping.items():
            w.setVisible(tid == tab_id)