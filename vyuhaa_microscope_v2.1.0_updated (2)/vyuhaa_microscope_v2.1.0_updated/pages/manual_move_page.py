"""
pages/manual_move_page.py
─────────────────────────
Manual stage movement with D-pad, Z focus buttons, 2x2 step-size selector,
live camera feed, focus quality bar, and automated image saving with gallery.

Vyuhaa API hooks marked with # [VYUHAA API].
"""

from __future__ import annotations
import os
from datetime import datetime
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QGridLayout, QSizePolicy, QDoubleSpinBox,
    QFileDialog, QDialog
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSize
from PySide6.QtGui import QFont, QImage, QPixmap, QIcon
import requests
import theme
import Vyuhaa_api as ofa   # [VYUHAA API]


# ── Gallery Dialog ────────────────────────────────────────────────────────────

class GalleryDialog(QDialog):
    """
    Popup window to browse saved images in the 'captures/' directory.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saved Captures")
        self.resize(800, 600)
        self.setStyleSheet(f"background:{theme.SURFACE}; color:{theme.WHITE};")
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("Image Gallery")
        header.setStyleSheet(f"font-family:'Syne'; font-size:20px; font-weight:700; color:{theme.TEAL}; margin-bottom:10px;")
        layout.addWidget(header)

        # Scrollable area for thumbnails
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{theme.SURFACE2}; border-radius:10px; }}")
        
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(15)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll)

        # Actions
        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet(theme.BTN_SECONDARY)
        refresh_btn.clicked.connect(self.refresh)
        
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(theme.BTN_PRIMARY)
        close_btn.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(refresh_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.refresh()

    def refresh(self):
        # Clear current grid
        for i in reversed(range(self.grid.count())): 
            self.grid.itemAt(i).widget().setParent(None)

        capture_dir = os.path.join(os.getcwd(), "captures")
        if not os.path.exists(capture_dir):
            os.makedirs(capture_dir, exist_ok=True)
            return

        files = [f for f in os.listdir(capture_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        files.sort(reverse=True) # newest first

        col_limit = 3
        for idx, filename in enumerate(files):
            path = os.path.join(capture_dir, filename)
            
            # Card
            card = QFrame()
            card.setStyleSheet(f"background:{theme.SURFACE}; border:1px solid {theme.BORDER}; border-radius:8px;")
            card_layout = QVBoxLayout(card)
            
            # Image
            thumb = QLabel()
            pix = QPixmap(path).scaled(200, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            thumb.setPixmap(pix)
            thumb.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(thumb)
            
            # Meta
            name_lbl = QLabel(filename)
            name_lbl.setStyleSheet(f"font-size:10px; color:{theme.TEXT_DIM};")
            name_lbl.setWordWrap(True)
            card_layout.addWidget(name_lbl)
            
            row = idx // col_limit
            col = idx % col_limit
            self.grid.addWidget(card, row, col)


# ── Workers ───────────────────────────────────────────────────────────────────

class MoveWorker(QThread):
    moved = Signal(object)
    error = Signal(str)

    def __init__(self, x=0, y=0, z=0):
        super().__init__()
        self.dx, self.dy, self.dz = x, y, z

    def run(self):
        try:
            pos = ofa.move_relative(dx=self.dx, dy=self.dy, dz=self.dz)
            self.moved.emit(pos)
        except Exception as e:
            self.error.emit(str(e))


class GotoWorker(QThread):
    moved = Signal(object)
    error = Signal(str)

    def __init__(self, x: float, y: float, z: float):
        super().__init__()
        self.x, self.y, self.z = x, y, z

    def run(self):
        try:
            pos = ofa.move_absolute(x=self.x, y=self.y, z=self.z)
            self.moved.emit(pos)
        except Exception as e:
            self.error.emit(str(e))


class AutofocusWorker(QThread):
    finished = Signal(float)
    error = Signal(str)

    def run(self):
        try:
            score = ofa.run_autofocus()
            self.finished.emit(score)
        except Exception as e:
            self.error.emit(str(e))


# ── Page ──────────────────────────────────────────────────────────────────────

class ManualMovePage(QWidget):
    position_changed = Signal(float, float, float)
    step_changed     = Signal(float)
    STEP_SIZES = [1, 10, 100, 1000]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x = self._y = self._z = 0.0
        self._step = 10.0
        self._active_workers: list[QThread] = []
        self._current_pixmap: QPixmap | None = None
        
        self._focus_poll_timer = QTimer(self)
        self._focus_poll_timer.setInterval(500)
        self._focus_poll_timer.timeout.connect(self._refresh_focus_score)
        
        # Build UI
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_camera_panel(), stretch=1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(160)
        sidebar.setStyleSheet(f"QFrame{{background:{theme.SURFACE}; border-right:1px solid {theme.BORDER};}}")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 12, 16, 0) # Reduced from 20, 24, 20, 0
        cl.setSpacing(0)

        # Title
        title = QLabel("Control")
        title.setStyleSheet(f"font-family:'Syne'; font-size:14px; font-weight:700; color:{theme.WHITE}; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid {theme.BORDER};")
        cl.addWidget(title)

        # ── COORDINATES SECTION (mm) ──────────────────────────────────────────
        coords_box = QFrame()
        coords_box.setStyleSheet(f"background:{theme.SURFACE2}; border:1px solid {theme.BORDER}; border-radius:8px;")
        cvl = QVBoxLayout(coords_box)
        cvl.setContentsMargins(8, 6, 8, 6)
        cvl.setSpacing(4)

        self._coord_spins: dict[str, QDoubleSpinBox] = {}
        for axis in ["X", "Y", "Z"]:
            row = QHBoxLayout()
            lbl = QLabel(axis)
            lbl.setFixedWidth(12)
            lbl.setStyleSheet(f"font-size:10px; color:{theme.TEAL}; font-weight:700; font-family:'JetBrains Mono';")
            
            spin = QDoubleSpinBox()
            spin.setRange(-999.0, 999.0)
            spin.setDecimals(3)
            spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
            spin.setFixedHeight(24)
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet(f"""
                QDoubleSpinBox {{ 
                    background:{theme.SURFACE}; border:1px solid {theme.BORDER}; 
                    border-radius:4px; color:{theme.WHITE}; font-size:9px; 
                    font-family:'JetBrains Mono'; font-weight:700;
                }}
            """)
            spin.editingFinished.connect(self._goto_position)
            self._coord_spins[axis] = spin
            row.addWidget(lbl); row.addWidget(spin); cvl.addLayout(row)
        
        cl.addWidget(coords_box)
        cl.addSpacing(10)
        
        cl.addSpacing(18)

        # Step Selector (2x2 Grid)
        # Step Selector (Grid)
        cl.addSpacing(10)
        step_grid = QGridLayout()
        step_grid.setSpacing(4)
        self.step_buttons = []
        for i, size in enumerate(self.STEP_SIZES):
            btn = QPushButton(f"{size}μm" if size < 1000 else "1mm")
            btn.setCheckable(True); btn.setChecked(size == 10); btn.setFixedHeight(24)
            btn.setStyleSheet(self._step_btn_style(size == 10))
            btn.clicked.connect(lambda _, s=size, b=btn: self._set_step(s, b))
            step_grid.addWidget(btn, i // 2, i % 2); self.step_buttons.append(btn)
        cl.addLayout(step_grid)
        cl.addSpacing(16)

        # Navigation Controls
        cl.addSpacing(10)
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        nav_row.addWidget(self._build_dpad())
        nav_row.addWidget(self._build_z_control())
        cl.addLayout(nav_row)
        cl.addSpacing(16)

        # Focus Bar
        cl.addWidget(self._build_focus_bar())
        cl.addStretch()

        scroll_area.setWidget(content)
        layout.addWidget(scroll_area, stretch=1)

        # Action Buttons
        actions = QFrame()
        actions.setStyleSheet(f"background:{theme.SURFACE}; border-top:1px solid {theme.BORDER};")
        al = QVBoxLayout(actions)
        al.setContentsMargins(10, 6, 10, 6)
        al.setSpacing(4)

        af_btn = QPushButton("AUTOFOCUS")
        af_btn.setFixedHeight(22)
        af_btn.setStyleSheet(theme.BTN_PRIMARY + " font-size:9px;")
        af_btn.clicked.connect(self._run_autofocus)
        
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        save_btn = QPushButton("SNAP")
        save_btn.setFixedHeight(22)
        save_btn.setStyleSheet(theme.BTN_PRIMARY + " font-size:9px; font-weight:700;")
        save_btn.clicked.connect(self._save_image)
        
        gallery_btn = QPushButton("🖼 GALLRY")
        gallery_btn.setFixedHeight(22)
        gallery_btn.setStyleSheet(theme.BTN_SECONDARY + f" background:{theme.SURFACE2}; border-color:{theme.TEAL}; color:{theme.TEAL}; font-weight:700; font-size:9px;")
        gallery_btn.clicked.connect(self._show_gallery)
        row2.addWidget(save_btn); row2.addWidget(gallery_btn)

        al.addWidget(af_btn); al.addLayout(row2)
        layout.addWidget(actions)

        return sidebar

    def _build_header(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size:10px; color:{theme.TEXT_DIM}; font-weight:700; letter-spacing:0.05em; margin-bottom:6px;")
        return lbl

    def _build_dpad(self) -> QWidget:
        w = QWidget(); g = QGridLayout(w); g.setSpacing(5); g.setContentsMargins(0, 0, 0, 0)
        style = f"QPushButton{{background:{theme.SURFACE2}; border:1px solid {theme.BORDER}; border-radius:10px; font-size:18px; color:{theme.TEXT_MID};}} " \
                f"QPushButton:hover{{border-color:{theme.TEAL}; color:{theme.TEAL};}} " \
                f"QPushButton:pressed{{background:rgba(30,184,168,0.1);}}"
        def _b(s, r, c, dx=0, dy=0):
            b = QPushButton(s); b.setFixedSize(28, 28); b.setStyleSheet(style)
            b.clicked.connect(lambda: self.move(dx, dy, 0)); g.addWidget(b, r, c)
        _b("↑", 0, 1, dy=1); _b("←", 1, 0, dx=-1)
        ctr = QLabel("XY"); ctr.setFixedSize(28, 28); ctr.setAlignment(Qt.AlignCenter)
        ctr.setStyleSheet(f"background:{theme.SURFACE}; border:1px solid {theme.BORDER}; border-radius:10px; font-size:9px; color:{theme.TEXT_DIM};")
        g.addWidget(ctr, 1, 1); _b("→", 1, 2, dx=1); _b("↓", 2, 1, dy=-1)
        return w

    def _build_z_control(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setAlignment(Qt.AlignCenter); l.setSpacing(5); l.setContentsMargins(0,0,0,0)
        style = f"QPushButton{{background:{theme.SURFACE2}; border:1px solid {theme.BORDER}; border-radius:10px; font-size:20px; font-weight:700; color:{theme.TEXT_MID};}}"
        z_up = QPushButton("+"); z_dn = QPushButton("−")
        for b in (z_up, z_dn): b.setFixedSize(28, 28); b.setStyleSheet(style)
        z_up.clicked.connect(lambda: self.move(0, 0, 1)); z_dn.clicked.connect(lambda: self.move(0, 0, -1))
        
        l.addWidget(z_up)
        l.addWidget(QLabel("Z", alignment=Qt.AlignCenter, styleSheet=f"color:{theme.TEAL}; font-family:'JetBrains Mono'; font-size:7px; font-weight:700;"))
        l.addWidget(z_dn)
        return w

    def _build_focus_bar(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0,0,0,0); l.setSpacing(4)
        h = QHBoxLayout(); h.addWidget(QLabel("Focus Quality", styleSheet=f"font-size:11px; color:{theme.TEXT_DIM};"))
        h.addStretch(); self.focus_score_lbl = QLabel("—", styleSheet=f"color:{theme.TEAL}; font-family:'JetBrains Mono';")
        h.addWidget(self.focus_score_lbl); l.addLayout(h)
        track = QFrame(); track.setFixedHeight(5); track.setStyleSheet(f"background:{theme.SURFACE2}; border-radius:3px;")
        self.focus_fill = QFrame(track); self.focus_fill.setGeometry(0,0,0,5)
        self.focus_fill.setStyleSheet(f"background:linear-gradient(to right, {theme.TEAL}, #25d4c2); border-radius:3px;")
        l.addWidget(track); self._focus_track = track; return w

    def _build_camera_panel(self) -> QWidget:
        self.cam_card = QFrame()
        self.cam_card.setStyleSheet("background:#050a0f;")
        
        # Give the camera layout zero margins so the image takes up 100% of the right-side area
        l = QVBoxLayout(self.cam_card)
        l.setContentsMargins(0, 0, 0, 0)
        
        self.camera_view = QLabel("CONNECTING TO FEED...")
        self.camera_view.setAlignment(Qt.AlignCenter)
        self.camera_view.setStyleSheet("font-family:'JetBrains Mono'; font-size:13px; color:rgba(255,255,255,0.1); letter-spacing:0.2em;")
        
        # Ensure the QLabel itself is allowed to expand fully in both directions
        self.camera_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        l.addWidget(self.camera_view)
        
        self._cam_poll_timer = QTimer(self)
        self._cam_poll_timer.setInterval(47) # Poll at ~21 FPS to match hardware output
        self._cam_poll_timer.timeout.connect(self._refresh_camera_feed)
        
        return self.cam_card

    def _step_btn_style(self, active: bool) -> str:
        base = f"font-size:9px; border-radius:6px; font-family:'JetBrains Mono'; padding:4px 2px;"
        if active: return f"QPushButton{{background:rgba(30,184,168,0.1); border:1px solid {theme.TEAL}; color:{theme.TEAL}; {base}}}"
        return f"QPushButton{{background:{theme.SURFACE2}; border:1px solid {theme.BORDER}; color:{theme.TEXT_DIM}; {base}}} QPushButton:hover{{border-color:{theme.TEAL}; color:{theme.TEAL};}}"

    def _set_step(self, size: float, btn: QPushButton):
        self._step = size
        for b in self.step_buttons: b.setStyleSheet(self._step_btn_style(b is btn))
        self.step_changed.emit(float(size))

    def move(self, dx, dy, dz):
        self._do_move(dx * self._step, dy * self._step, dz * self._step)

    def _do_move(self, dx, dy, dz):
        self._active_workers = [w for w in self._active_workers if w.isRunning()]
        if self._active_workers: return
        worker = MoveWorker(x=dx, y=dy, z=dz)
        worker.moved.connect(self._on_moved); worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._active_workers.append(worker); worker.start()

    def _goto_position(self):
        # [VYUHAA API] Move to absolute mm coordinates (convert to µm for hardware)
        tx = self._coord_spins["X"].value() * 1000.0
        ty = self._coord_spins["Y"].value() * 1000.0
        tz = self._coord_spins["Z"].value() * 1000.0
        
        worker = GotoWorker(x=tx, y=ty, z=tz)
        worker.moved.connect(self._on_moved)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._active_workers.append(worker); worker.start()

    def _save_image(self):
        """[VYUHAA API] Saves current frame to 'captures/' with coordinate stamp."""
        if not self._current_pixmap: return
        
        cap_dir = os.path.join(os.getcwd(), "captures")
        os.makedirs(cap_dir, exist_ok=True)
        
        # Create unique filename with coordinate stamp
        stamp = datetime.now().strftime("%H%M%S")
        fn = f"cap_X{int(self._x)}_Y{int(self._y)}_Z{int(self._z)}_{stamp}.png"
        path = os.path.join(cap_dir, fn)
        
        if self._current_pixmap.save(path):
            print(f"Captured: {fn}")
        else:
            print("Capture Failed.")

    def _show_gallery(self):
        GalleryDialog(self).exec()

    def _cleanup_worker(self, w):
        if w in self._active_workers: self._active_workers.remove(w)
        w.deleteLater()

    def _on_moved(self, pos):
        # pos is in raw µm from hardware
        self._x, self._y, self._z = pos.x, pos.y, pos.z
        
        # Update spinboxes in mm (block signals to avoid feedback loop)
        for axis, val in [("X", self._x), ("Y", self._y), ("Z", self._z)]:
            spin = self._coord_spins[axis]
            spin.blockSignals(True)
            spin.setValue(val / 1000.0)
            spin.blockSignals(False)
            
        self.position_changed.emit(self._x, self._y, self._z)

    def zero_coords(self):
        ofa.zero_stage()
        self._x = self._y = self._z = 0.0
        # Manual pos object for sync
        class P: pass
        p = P(); p.x = p.y = p.z = 0.0
        self._on_moved(p)

    def _run_autofocus(self):
        if any(isinstance(w, AutofocusWorker) and w.isRunning() for w in self._active_workers): return
        self.focus_score_lbl.setText("Focusing…")
        worker = AutofocusWorker()
        worker.finished.connect(self._on_autofocus_finished); worker.finished.connect(lambda: self._cleanup_worker(worker))
        self._active_workers.append(worker); worker.start()

    def _on_autofocus_finished(self, score):
        self._update_focus_display(score)
        try:
            p = ofa.get_position()
            self._on_moved(p)
        except: pass

    def _refresh_focus_score(self):
        try:
            s = ofa.get_focus_score()
            self._update_focus_display(s)
        except: pass

    def _update_focus_display(self, s):
        q = "GOOD" if s > 300 else "FAIR" if s > 100 else "POOR"
        self.focus_score_lbl.setText(f"{s:.0f} — {q}")


    def _refresh_camera_feed(self):
        try:
            arr = ofa.grab_frame_array()
            if arr is not None:
                h, w, ch = arr.shape
                img = QImage(arr.data, w, h, ch*w, QImage.Format_RGB888).copy()
                pix = QPixmap.fromImage(img)
                self._current_pixmap = pix
                
                # By ignore aspect ratio here (Qt.IgnoreAspectRatio), the image will perfectly
                # fill the 800x480 (minus sidebar) screen space, showing maximum FOV. 
                # Since the camera is 1280x720 (16:9), and the viewing area is roughly 640x480 (4:3),
                # Qt.KeepAspectRatio was crushing the image to fit the width, leaving massive black bars on top and bottom.
                self.camera_view.setPixmap(pix.scaled(
                    self.camera_view.size(), 
                    Qt.IgnoreAspectRatio, 
                    Qt.SmoothTransformation
                ))
        except: pass

    def on_page_activated(self):
        self._focus_poll_timer.start()
        if ofa.get_connection_status(): self._cam_poll_timer.start()
        else: self.camera_view.setText("FEED OFFLINE")

    def set_position_remote(self, x: float, y: float, z: float) -> None:
        """Update position display from a remote client command (move_to/move/zero).
        Does NOT re-emit position_changed to avoid broadcast loop."""
        self._x, self._y, self._z = float(x), float(y), float(z)
        for axis, val in [("X", self._x), ("Y", self._y), ("Z", self._z)]:
            spin = self._coord_spins[axis]
            spin.blockSignals(True)
            spin.setValue(val / 1000.0)
            spin.blockSignals(False)

    def set_step_remote(self, step: float) -> None:
        """Update step size from a remote client without re-emitting step_changed."""
        self._step = float(step)
        for b in self.step_buttons:
            txt = b.text()
            val = 1000.0 if txt == "1mm" else float(txt.replace("μm", "").strip() or "-1")
            b.setStyleSheet(self._step_btn_style(abs(val - self._step) < 0.01))

    def on_page_deactivated(self):
        self._focus_poll_timer.stop(); self._cam_poll_timer.stop()
        for w in self._active_workers:
            if w.isRunning(): w.wait()