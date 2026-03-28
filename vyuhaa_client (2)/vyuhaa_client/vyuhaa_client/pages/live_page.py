"""
Vyuhaa Remote Client — Live View Page
Two-panel: sidebar (stage controls + focus) + main (camera feed + operator chat).
"""

from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QSizePolicy, QScrollArea, QLineEdit,
    QGridLayout, QSpacerItem, QProgressBar
)
from PySide6.QtCore import Qt, Signal, QTimer, QRect
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QRadialGradient
from styles import TEAL, GREEN, RED, BORDER, SURFACE, SURFACE2, BG, TEXT_DIM, TEXT_MID, WHITE


# ── Camera feed (simulated) ───────────────────────────────────────────────────

class CameraFeed(QWidget):
    capture_clicked = Signal()
    scan_clicked    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fps = 30
        self._live_pixmap = None        # raw frame from server
        self._scaled_pixmap = None      # cached scaled version
        self._scaled_size = (0, 0)      # widget size when scaled was computed
        # Repaint timer — only for animated fallback blobs when not connected
        from PySide6.QtCore import QTimer
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(100)
        self._repaint_timer.timeout.connect(self._repaint_if_no_feed)
        self._repaint_timer.start()
        self._cells = [
            (0.38, 0.40, 19, 15, 0.09),
            (0.55, 0.50, 14, 17, 0.07),
            (0.63, 0.30, 11, 12, 0.08),
            (0.42, 0.62, 16, 12, 0.06),
            (0.28, 0.54, 20, 17, 0.10),
            (0.48, 0.24, 9,  10, 0.06),
            (0.66, 0.70, 12, 14, 0.05),
        ]

    def set_frame(self, jpeg_bytes: bytes) -> None:
        """Display a real JPEG frame received from the microscope server."""
        from PySide6.QtGui import QPixmap
        pix = QPixmap()
        if pix.loadFromData(jpeg_bytes) and not pix.isNull():
            self._live_pixmap = pix
            self._scaled_pixmap = None  # invalidate cache
            self.update()

    def _repaint_if_no_feed(self) -> None:
        """Animate the fallback blobs; skip if a real feed is showing."""
        if self._live_pixmap is None:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # ── Display real server frame if available ────────────────────────
        if self._live_pixmap is not None:
            if self._scaled_pixmap is None or self._scaled_size != (w, h):
                self._scaled_pixmap = self._live_pixmap.scaled(
                    w, h, Qt.KeepAspectRatio, Qt.FastTransformation
                )
                self._scaled_size = (w, h)
            scaled = self._scaled_pixmap
            x = (w - scaled.width())  // 2
            y = (h - scaled.height()) // 2
            p.fillRect(0, 0, w, h, QColor("#050a0f"))
            p.drawPixmap(x, y, scaled)
            p.end()
            return

        # ── Fallback: simulated blobs when not connected ──────────────────
        p.fillRect(0, 0, w, h, QColor("#050a0f"))

        grid_col = QColor(TEAL)
        grid_col.setAlphaF(0.03)
        pen = QPen(grid_col, 1)
        p.setPen(pen)
        step = 40
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)

        for cx_f, cy_f, rx, ry, alpha in self._cells:
            cx, cy = int(cx_f * w), int(cy_f * h)
            grad = QRadialGradient(cx, cy, max(rx, ry))
            c = QColor(TEAL); c.setAlphaF(alpha)
            t = QColor(TEAL); t.setAlphaF(0)
            grad.setColorAt(0, c)
            grad.setColorAt(1, t)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.NoPen)
            p.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)

        scan_col = QColor(0, 0, 0, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(scan_col))
        for y in range(2, h, 3):
            p.drawRect(0, y, w, 1)

        teal_col = QColor(TEAL); teal_col.setAlphaF(0.4)
        pen2 = QPen(teal_col, 2)
        p.setPen(pen2)
        p.setBrush(Qt.NoBrush)
        size = 16
        off  = 20
        for (cx2, cy2, dx, dy) in [
            (off,   off,   1,  1),
            (w-off, off,  -1,  1),
            (off,   h-off, 1, -1),
            (w-off, h-off,-1, -1),
        ]:
            p.drawLine(cx2, cy2, cx2 + dx * size, cy2)
            p.drawLine(cx2, cy2, cx2, cy2 + dy * size)

        ch_col = QColor(TEAL); ch_col.setAlphaF(0.35)
        p.setPen(QPen(ch_col, 1))
        cx3, cy3 = w // 2, h // 2
        p.drawLine(cx3, cy3 - 20, cx3, cy3 + 20)
        p.drawLine(cx3 - 20, cy3, cx3 + 20, cy3)
        p.drawEllipse(cx3 - 5, cy3 - 5, 10, 10)

        p.end()


# ── Position box ─────────────────────────────────────────────────────────────

class PosBox(QFrame):
    value_submitted = Signal(str, float)

    def __init__(self, axis: str, value: float = 0.0, parent=None):
        super().__init__(parent)
        self._axis = axis.lower()
        self.setStyleSheet(
            f"QFrame {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; }}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(2)
        v.setAlignment(Qt.AlignCenter)

        ax = QLabel(axis)
        ax.setAlignment(Qt.AlignCenter)
        ax.setObjectName("AxisLabel")
        ax.setStyleSheet(f"color: {TEAL}; font-weight: 700; font-size: 11px;")

        self.val = QLineEdit(f"{value:.1f}")
        self.val.setAlignment(Qt.AlignCenter)
        self.val.setObjectName("PosInput")
        self.val.setStyleSheet(
            f"QLineEdit {{"
            f"  font-family: 'JetBrains Mono', monospace; font-size: 16px; "
            f"  font-weight: 500; color: #ffffff; "
            f"  background: rgba(0, 0, 0, 0.3); border: 1px solid {TEAL}; "
            f"  border-radius: 6px; padding: 4px; "
            f"}}"
            f"QLineEdit:focus {{"
            f"  background: rgba(30, 184, 168, 0.1); border: 2px solid {TEAL}; "
            f"}}"
        )
        self.val.returnPressed.connect(self._submit)
        self.val.editingFinished.connect(self._submit)
        self.val.setMaxLength(12)
        self.val.setFixedHeight(40)

        unit = QLabel("μm")
        unit.setAlignment(Qt.AlignCenter)
        unit.setObjectName("UnitLabel")
        unit.setStyleSheet(f"font-size: 9px; color: {TEXT_DIM};")

        v.addWidget(ax); v.addWidget(self.val); v.addWidget(unit)

    def set_value(self, v: float):
        # Only update if field is not currently being edited
        if self.val.hasFocus():
            return
        self.val.setText(f"{v:.1f}")

    def _submit(self):
        text = self.val.text().strip()
        if not text:
            return
        try:
            value = float(text)
            self.value_submitted.emit(self._axis, value)
            self.val.clearFocus()
        except ValueError:
            # Reset to previous value on invalid input
            self.val.undo()


# ── Chat message ─────────────────────────────────────────────────────────────

class ChatMessage(QFrame):
    def __init__(self, who: str, text: str, is_me: bool = False, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(8)

        badge_bg   = "rgba(30,184,168,0.12)"   if is_me else "rgba(100,116,139,0.12)"
        badge_fg   = TEAL                        if is_me else TEXT_MID
        badge_bord = "rgba(30,184,168,0.3)"      if is_me else BORDER

        who_lbl = QLabel(who)
        who_lbl.setFixedWidth(36)
        who_lbl.setAlignment(Qt.AlignCenter)
        who_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 700; "
            f"color: {badge_fg}; background: {badge_bg}; border: 1px solid {badge_bord}; "
            f"border-radius: 5px; padding: 2px 4px;"
        )
        msg_lbl = QLabel(text)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"font-size: 12px; color: #e2e8f0; background: transparent;")
        msg_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        if is_me:
            h.addStretch(); h.addWidget(msg_lbl); h.addWidget(who_lbl)
        else:
            h.addWidget(who_lbl); h.addWidget(msg_lbl); h.addStretch()


# ── Live Page ─────────────────────────────────────────────────────────────────

class LivePage(QWidget):
    """
    Signals
    -------
    move_requested(axis, direction)  — axis='x'|'y'|'z', direction=+1|-1
    autofocus_requested()
    zero_requested()
    capture_requested()
    stream_toggled(bool)
    quality_changed(str)             — 'SD'|'HD'|'4K'
    end_session()
    operator_message(str)
    """

    move_requested    = Signal(str, int)
    autofocus_requested = Signal()
    zero_requested    = Signal()
    capture_requested = Signal()
    stream_toggled    = Signal(bool)
    quality_changed   = Signal(str)
    position_set_requested = Signal(float, float, float)
    step_size_requested = Signal(float)
    end_session       = Signal()
    operator_message  = Signal(str)
    home_requested    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._step       = 10.0
        self._streaming  = True
        self._recording  = False
        self._pos        = [142.5, -88.0, 10.0]
        self._focus      = 0.73
        self._step_btns  = []
        self._quality_btns = []
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────────────
        sidebar = QFrame(); sidebar.setObjectName("SideBar")
        sidebar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sidebar.setMinimumWidth(260)
        sv = QVBoxLayout(sidebar); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(0)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sw = QWidget()
        sv2 = QVBoxLayout(sw); sv2.setContentsMargins(20, 24, 20, 16); sv2.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title = QLabel("Live View"); title.setObjectName("SidebarTitle")
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
        sep.setStyleSheet(f"background: {BORDER}; max-height: 1px; border: none;")
        sv2.addWidget(sep)
        sv2.addSpacing(4)

        # Position grid
        pos_grid = QGridLayout(); pos_grid.setSpacing(8)
        self.pos_x = PosBox("X", 142.5); self.pos_y = PosBox("Y", -88.0); self.pos_z = PosBox("Z", 10.0)
        self.pos_x.value_submitted.connect(self._on_manual_position_submit)
        self.pos_y.value_submitted.connect(self._on_manual_position_submit)
        self.pos_z.value_submitted.connect(self._on_manual_position_submit)
        pos_grid.addWidget(self.pos_x, 0, 0)
        pos_grid.addWidget(self.pos_y, 0, 1)
        pos_grid.addWidget(self.pos_z, 0, 2)
        sv2.addLayout(pos_grid)

        # Step selector
        step_lbl = QLabel("STEP SIZE"); step_lbl.setObjectName("FieldLabel")
        sv2.addWidget(step_lbl)
        step_row = QHBoxLayout(); step_row.setSpacing(6)
        for val, label in ((1,"1 μm"),(10,"10 μm"),(100,"100 μm"),(1000,"1 mm")):
            b = QPushButton(label)
            b.setObjectName("StepBtnActive" if val == 10 else "StepBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _, v=val, btn=b: self._set_step(v, btn))
            self._step_btns.append(b)
            step_row.addWidget(b)
        sv2.addLayout(step_row)

        # D-pad + Z controls
        dpad_row = QHBoxLayout(); dpad_row.setSpacing(16); dpad_row.setAlignment(Qt.AlignCenter)
        dpad_grid = QGridLayout(); dpad_grid.setSpacing(5)
        moves = {(0,1):("↑","y",1),(1,0):("←","x",-1),(1,1):("XY",None,0),(1,2):("→","x",1),(2,1):("↓","y",-1)}
        for (r,c),(lbl,ax,d) in moves.items():
            b = QPushButton(lbl)
            if ax is None:
                b.setEnabled(False)
                b.setStyleSheet(
                    f"background:{SURFACE}; border:1px solid {BORDER}; border-radius:12px; "
                    f"color:{TEXT_DIM}; font-size:11px; font-family:'JetBrains Mono',monospace;"
                    "padding:0;"
                )
            else:
                b.setObjectName("DPadBtn")
                b.clicked.connect(lambda _, a=ax, dr=d: self.move_requested.emit(a, dr))
                b.setCursor(Qt.PointingHandCursor)
                b.setFocusPolicy(Qt.NoFocus)
            b.setFixedSize(58, 58)
            dpad_grid.addWidget(b, r, c)
        for (r,c) in ((0,0),(0,2),(2,0),(2,2)):
            empty = QWidget(); empty.setFixedSize(58, 58)
            dpad_grid.addWidget(empty, r, c)

        z_col = QVBoxLayout(); z_col.setSpacing(6); z_col.setAlignment(Qt.AlignCenter)
        z_lbl = QLabel("FOCUS Z"); z_lbl.setObjectName("AxisLabel"); z_lbl.setAlignment(Qt.AlignCenter)
        z_up  = QPushButton("+"); z_up.setObjectName("ZBtn"); z_up.setFixedSize(58, 58)
        z_up.setCursor(Qt.PointingHandCursor); z_up.setFocusPolicy(Qt.NoFocus)
        z_up.clicked.connect(lambda: self.move_requested.emit("z", 1))
        z_dn  = QPushButton("−"); z_dn.setObjectName("ZBtn"); z_dn.setFixedSize(58, 58)
        z_dn.setCursor(Qt.PointingHandCursor); z_dn.setFocusPolicy(Qt.NoFocus)
        z_dn.clicked.connect(lambda: self.move_requested.emit("z", -1))
        z_col.addWidget(z_lbl); z_col.addWidget(z_up); z_col.addSpacing(10); z_col.addWidget(z_dn)

        dpad_row.addLayout(dpad_grid); dpad_row.addLayout(z_col)
        sv2.addLayout(dpad_row)

        hint = QLabel("W A S D · + − focus")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"font-size:10px; font-family:'JetBrains Mono',monospace; color:{TEXT_DIM}; background:transparent;")
        sv2.addWidget(hint)

        # Focus bar
        foc_h = QHBoxLayout()
        foc_lbl   = QLabel("Focus Quality")
        foc_lbl.setStyleSheet(f"font-size:11px; color:{TEXT_DIM}; background:transparent;")
        self.foc_score = QLabel("442 — GOOD")
        self.foc_score.setStyleSheet(
            f"font-family:'JetBrains Mono',monospace; font-size:12px; color:{TEAL}; background:transparent;"
        )
        foc_h.addWidget(foc_lbl); foc_h.addStretch(); foc_h.addWidget(self.foc_score)
        sv2.addLayout(foc_h)

        self.focus_bar = QProgressBar()
        self.focus_bar.setRange(0, 100); self.focus_bar.setValue(73)
        self.focus_bar.setFixedHeight(5)
        self.focus_bar.setTextVisible(False)
        sv2.addWidget(self.focus_bar)
        sv2.addStretch()

        scroll.setWidget(sw)
        sv.addWidget(scroll)

        # Sidebar buttons
        ab = QFrame(); ab.setObjectName("SidebarActionsBar")
        ab_v = QVBoxLayout(ab); ab_v.setContentsMargins(20,12,20,12); ab_v.setSpacing(8)
        af_btn = QPushButton("⟳   Autofocus"); af_btn.setObjectName("BtnPrimary"); af_btn.setFixedHeight(40)
        af_btn.setCursor(Qt.PointingHandCursor); af_btn.clicked.connect(self.autofocus_requested)
        zero_btn = QPushButton("⊙   Zero Coordinates"); zero_btn.setObjectName("BtnSecondary"); zero_btn.setFixedHeight(40)
        zero_btn.setCursor(Qt.PointingHandCursor); zero_btn.clicked.connect(self.zero_requested)
        end_btn = QPushButton("✕   End Session"); end_btn.setObjectName("BtnDanger"); end_btn.setFixedHeight(36)
        end_btn.setCursor(Qt.PointingHandCursor); end_btn.clicked.connect(self.end_session)
        ab_v.addWidget(af_btn); ab_v.addWidget(zero_btn); ab_v.addWidget(end_btn)
        sv.addWidget(ab)
        root.addWidget(sidebar)

        # ── Main panel ────────────────────────────────────────────────────
        main = QWidget()
        main_v = QVBoxLayout(main); main_v.setContentsMargins(0,0,0,0); main_v.setSpacing(0)

        self.camera = CameraFeed(); self.camera.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cam_container = QWidget(); cam_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cam_v = QVBoxLayout(cam_container); cam_v.setContentsMargins(0,0,0,0)
        cam_v.addWidget(self.camera)
        main_v.addWidget(cam_container, stretch=1)

        # Live toolbar
        tb = QFrame(); tb.setObjectName("LiveToolbar"); tb.setFixedHeight(44)
        tb_h = QHBoxLayout(tb); tb_h.setContentsMargins(12,0,12,0); tb_h.setSpacing(4)

        self.stream_btn = QPushButton("🎥 Stream"); self.stream_btn.setObjectName("TbBtnActive")
        self.stream_btn.setCursor(Qt.PointingHandCursor); self.stream_btn.clicked.connect(self._toggle_stream)
        cap_btn = QPushButton("📷 Capture"); cap_btn.setObjectName("TbBtn")
        cap_btn.setCursor(Qt.PointingHandCursor); cap_btn.clicked.connect(self.capture_requested)
        self.rec_btn = QPushButton("⏺ Record"); self.rec_btn.setObjectName("TbBtn")
        self.rec_btn.setCursor(Qt.PointingHandCursor); self.rec_btn.clicked.connect(self._toggle_record)
        cross_btn = QPushButton("⊕ Crosshair"); cross_btn.setObjectName("TbBtn"); cross_btn.setCursor(Qt.PointingHandCursor)
        meas_btn  = QPushButton("📏 Measure");  meas_btn.setObjectName("TbBtn");  meas_btn.setCursor(Qt.PointingHandCursor)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.VLine)
        sep1.setStyleSheet(f"color:{BORDER};background:{BORDER};border:none;max-width:1px;")
        sep2 = QFrame(); sep2.setFrameShape(QFrame.VLine)
        sep2.setStyleSheet(f"color:{BORDER};background:{BORDER};border:none;max-width:1px;")
        sep3 = QFrame(); sep3.setFrameShape(QFrame.VLine)
        sep3.setStyleSheet(f"color:{BORDER};background:{BORDER};border:none;max-width:1px;")

        qual_lbl = QLabel("QUALITY")
        qual_lbl.setStyleSheet(f"font-size:10px; font-family:'JetBrains Mono',monospace; color:{TEXT_DIM}; background:transparent;")
        qual_row = QHBoxLayout(); qual_row.setSpacing(3)
        for q in ("SD","HD","4K"):
            b = QPushButton(q)
            b.setObjectName("TbBtnActive" if q=="HD" else "TbBtn")
            b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _, qv=q, btn=b: self._set_quality(qv, btn))
            b.setFixedHeight(28)
            self._quality_btns.append(b)
            qual_row.addWidget(b)

        end_tb = QPushButton("✕ End Session"); end_tb.setObjectName("TbBtnDanger")
        end_tb.setCursor(Qt.PointingHandCursor); end_tb.clicked.connect(self.end_session)

        tb_h.addWidget(self.stream_btn); tb_h.addWidget(sep1)
        tb_h.addWidget(cap_btn); tb_h.addWidget(self.rec_btn); tb_h.addWidget(sep2)
        tb_h.addWidget(cross_btn); tb_h.addWidget(meas_btn); tb_h.addWidget(sep3)
        tb_h.addWidget(qual_lbl); tb_h.addLayout(qual_row)
        tb_h.addStretch(); tb_h.addWidget(end_tb)
        main_v.addWidget(tb)

        # Operator panel
        op = QFrame(); op.setObjectName("OpPanel")
        op_v = QVBoxLayout(op); op_v.setContentsMargins(0,0,0,0); op_v.setSpacing(0)

        op_header = QFrame(); op_header.setObjectName("OpPanelHeader"); op_header.setFixedHeight(38)
        op_h = QHBoxLayout(op_header); op_h.setContentsMargins(14,0,14,0); op_h.setSpacing(10)
        op_dot = QLabel("●")
        op_dot.setStyleSheet(f"color:{GREEN}; font-size:9px; background:transparent;")
        op_name = QLabel("Arun (On-site Operator)")
        op_name.setStyleSheet(f"font-size:12px; font-weight:600; color:#e2e8f0; background:transparent;")
        op_state = QLabel("Slide loaded — ready")
        op_state.setStyleSheet(f"font-size:11px; color:{TEXT_DIM}; background:transparent;")
        self.op_chevron = QPushButton("▲"); self.op_chevron.setObjectName("TbBtn")
        self.op_chevron.setFixedWidth(28); self.op_chevron.clicked.connect(self._toggle_op_panel)
        op_h.addWidget(op_dot); op_h.addWidget(op_name); op_h.addWidget(op_state)
        op_h.addStretch(); op_h.addWidget(self.op_chevron)
        op_v.addWidget(op_header)

        self.op_body = QWidget()
        ob_v = QVBoxLayout(self.op_body); ob_v.setContentsMargins(0,0,0,0); ob_v.setSpacing(0)

        self.chat_scroll = QScrollArea(); self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.NoFrame); self.chat_scroll.setFixedHeight(100)
        chat_w = QWidget()
        self.chat_layout = QVBoxLayout(chat_w); self.chat_layout.setContentsMargins(14,8,14,8)
        self.chat_layout.setSpacing(4); self.chat_layout.addStretch()
        self.chat_scroll.setWidget(chat_w)
        ob_v.addWidget(self.chat_scroll)

        input_row = QHBoxLayout(); input_row.setContentsMargins(12,6,12,10); input_row.setSpacing(8)
        self.op_input = QLineEdit(); self.op_input.setPlaceholderText("Message operator…")
        self.op_input.setFixedHeight(34)
        self.op_input.returnPressed.connect(self._send_op_msg)
        send_btn = QPushButton("↑"); send_btn.setObjectName("SendBtn"); send_btn.setFixedSize(34,34)
        send_btn.setCursor(Qt.PointingHandCursor); send_btn.clicked.connect(self._send_op_msg)
        input_row.addWidget(self.op_input); input_row.addWidget(send_btn)
        ob_v.addLayout(input_row)
        op_v.addWidget(self.op_body)

        main_v.addWidget(op)
        root.addWidget(main, stretch=1)

        # Seed chat messages
        for who, text, is_me in [
            ("OPR", "Slide loaded and clipped in. Ready when you are.", False),
            ("YOU", "Thanks — moving to center position now.",          True),
            ("OPR", "Looks good from here. Light level okay?",          False),
        ]:
            self._add_chat_msg(who, text, is_me)

    # ── Slots ─────────────────────────────────────────────────────────────

    def _set_step(self, val: int, btn: QPushButton):
        self.update_step_size(float(val), emit_signal=False)
        self.step_size_requested.emit(float(val))
        # Visual feedback
        btn.setStyleSheet(btn.styleSheet() + f"; font-weight: 700; opacity: 1.0;")

    def update_step_size(self, step_um: float, emit_signal: bool = False):
        self._step = float(step_um)
        active_btn = None
        for b in self._step_btns:
            label = b.text().strip().lower()
            if (
                (self._step == 1 and label.startswith("1 μm")) or
                (self._step == 10 and label.startswith("10 μm")) or
                (self._step == 100 and label.startswith("100 μm")) or
                (self._step == 1000 and label.startswith("1 mm"))
            ):
                active_btn = b
        for b in self._step_btns:
            b.setObjectName("StepBtn")
            b.style().unpolish(b); b.style().polish(b)
        if active_btn is not None:
            active_btn.setObjectName("StepBtnActive")
            active_btn.style().unpolish(active_btn); active_btn.style().polish(active_btn)
        if emit_signal:
            self.step_size_requested.emit(self._step)

    def _set_quality(self, q: str, btn: QPushButton):
        for b in self._quality_btns:
            b.setObjectName("TbBtn")
            b.style().unpolish(b); b.style().polish(b)
        btn.setObjectName("TbBtnActive")
        btn.style().unpolish(btn); btn.style().polish(btn)
        self.quality_changed.emit(q)

    def _toggle_stream(self):
        self._streaming = not self._streaming
        self.stream_btn.setObjectName("TbBtnActive" if self._streaming else "TbBtn")
        self.stream_btn.style().unpolish(self.stream_btn)
        self.stream_btn.style().polish(self.stream_btn)
        self.stream_toggled.emit(self._streaming)

    def _toggle_record(self):
        self._recording = not self._recording
        self.rec_btn.setObjectName("TbBtnActive" if self._recording else "TbBtn")
        self.rec_btn.style().unpolish(self.rec_btn)
        self.rec_btn.style().polish(self.rec_btn)

    def _toggle_op_panel(self):
        vis = self.op_body.isVisible()
        self.op_body.setVisible(not vis)
        self.op_chevron.setText("▲" if not vis else "▼")

    def _send_op_msg(self):
        text = self.op_input.text().strip()
        if text:
            self._add_chat_msg("YOU", text, is_me=True)
            self.operator_message.emit(text)
            self.op_input.clear()

    def _add_chat_msg(self, who: str, text: str, is_me: bool):
        msg = ChatMessage(who, text, is_me)
        idx = self.chat_layout.count() - 1
        self.chat_layout.insertWidget(idx, msg)
        QTimer.singleShot(50, lambda: self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        ))

    # ── Public API ─────────────────────────────────────────────────────────

    def update_position(self, x: float, y: float, z: float):
        print(f"[SYNC-CLIENT] live_page.update_position x={x} y={y} z={z}")
        self._pos = [x, y, z]
        self.pos_x.set_value(x); self.pos_y.set_value(y); self.pos_z.set_value(z)

    def _on_manual_position_submit(self, axis: str, value: float):
        idx = {"x": 0, "y": 1, "z": 2}.get(axis)
        if idx is None:
            return
        self._pos[idx] = float(value)
        self.position_set_requested.emit(self._pos[0], self._pos[1], self._pos[2])

    def update_focus(self, score: float, label: str = ""):
        self.focus_bar.setValue(int(score * 100))
        if label:
            self.foc_score.setText(f"{int(score*1000)} — {label}")
