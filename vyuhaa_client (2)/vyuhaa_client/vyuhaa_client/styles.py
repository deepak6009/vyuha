"""
Vyuhaa Remote Client — Design System / QSS Stylesheet
Mirrors the CSS variables from vyuhaa-remote-client-v3.html
"""

# ── Colour tokens ──────────────────────────────────────────────
C = {
    "bg":           "#0d1117",
    "surface":      "#161b22",
    "surface2":     "#1c2330",
    "border":       "#2a3441",
    "teal":         "#1eb8a8",
    "red":          "#ef4444",
    "green":        "#22c55e",
    "amber":        "#f59e0b",
    "blue":         "#3b82f6",
    "text":         "#e2e8f0",
    "text_dim":     "#64748b",
    "text_mid":     "#94a3b8",
    "white":        "#ffffff",
}

# Also expose as PALETTE for widgets that import it
PALETTE = C

APP_STYLE = f"""
QWidget {{
    background-color: {C['bg']};
    color: {C['text']};
    font-family: 'DM Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
    border: none;
    outline: none;
}}
QScrollBar:vertical {{
    background: {C['surface']};
    width: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {C['border']};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{
    background: {C['surface']};
    height: 6px;
    border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {C['border']};
    border-radius: 3px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

QFrame#SideBar {{
    background-color: {C['surface']};
    border-right: 1px solid {C['border']};
}}
QFrame#TopBar {{
    background-color: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}
QFrame#BottomBar {{
    background-color: {C['surface']};
    border-top: 1px solid {C['border']};
}}
QFrame#SidebarActionsBar {{
    background-color: {C['surface']};
    border-top: 1px solid {C['border']};
}}
QFrame#LogStrip {{
    background-color: {C['surface']};
    border-top: 1px solid {C['border']};
}}
QFrame#LiveToolbar {{
    background-color: {C['surface']};
    border-top: 1px solid {C['border']};
}}
QFrame#OpPanel {{
    background-color: {C['surface']};
    border-top: 1px solid {C['border']};
}}
QFrame#OpPanelHeader {{
    background-color: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}
QFrame#ScanProgressStrip {{
    background-color: {C['surface']};
    border-top: 1px solid {C['border']};
}}
QFrame#FilesToolbar {{
    background-color: {C['surface']};
    border-bottom: 1px solid {C['border']};
}}

QLabel#AppTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {C['white']};
    letter-spacing: 0.5px;
}}
QLabel#SidebarTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {C['white']};
}}
QLabel#SidebarSub {{
    font-size: 12px;
    color: {C['text_dim']};
    line-height: 1.5;
}}
QLabel#FieldLabel {{
    font-size: 11px;
    color: {C['text_dim']};
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#MonoLabel {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 11px;
    color: {C['text_dim']};
}}
QLabel#MonoValue {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 17px;
    font-weight: 500;
    color: {C['white']};
}}
QLabel#AxisLabel {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 10px;
    color: {C['teal']};
    letter-spacing: 2px;
}}
QLabel#UnitLabel {{
    font-size: 10px;
    color: {C['text_dim']};
}}
QLabel#LogText {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 11px;
    color: {C['text_mid']};
}}
QLabel#HudChip {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 10px;
    color: {C['text_mid']};
    background-color: rgba(5,10,15,0.85);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px;
    padding: 4px 10px;
}}
QLabel#StatLabel {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 9px;
    color: {C['text_dim']};
    letter-spacing: 1px;
}}
QLabel#StatValue {{
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 15px;
    font-weight: 600;
    color: {C['white']};
}}

QLineEdit {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 10px 14px;
    color: {C['text']};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 13px;
    selection-background-color: {C['teal']};
    selection-color: #000;
}}
QLineEdit:focus {{ border-color: {C['teal']}; }}
QPlainTextEdit {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 8px 12px;
    color: {C['text']};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 12px;
}}
QPlainTextEdit:focus {{ border-color: {C['teal']}; }}
QComboBox {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 10px 14px;
    color: {C['text']};
    font-size: 13px;
}}
QComboBox:focus {{ border-color: {C['teal']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    selection-background-color: {C['teal']};
    selection-color: #000;
    border-radius: 8px;
    outline: none;
}}
QSpinBox {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 8px 10px;
    color: {C['text']};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 14px;
}}
QSpinBox:focus {{ border-color: {C['teal']}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {C['surface']};
    border: none;
    width: 18px;
}}
QSlider::groove:horizontal {{
    background: {C['surface2']};
    height: 4px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C['teal']};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: {C['teal']};
    border-radius: 2px;
}}

QPushButton {{
    border: none;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
    padding: 12px 20px;
}}
QPushButton#BtnPrimary {{
    background-color: {C['teal']};
    color: #000000;
    font-weight: 700;
    border-radius: 12px;
}}
QPushButton#BtnPrimary:hover {{ background-color: #25d4c2; }}
QPushButton#BtnPrimary:pressed {{ background-color: #19a090; }}
QPushButton#BtnPrimary:disabled {{
    background-color: {C['surface2']};
    color: {C['text_dim']};
}}
QPushButton#BtnSecondary {{
    background-color: {C['surface2']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 12px;
}}
QPushButton#BtnSecondary:hover {{
    border-color: {C['teal']};
    color: {C['teal']};
}}
QPushButton#BtnSecondary:pressed {{ background-color: {C['surface']}; }}
QPushButton#BtnDanger {{
    background-color: rgba(239,68,68,0.12);
    color: {C['red']};
    border: 1px solid rgba(239,68,68,0.25);
    border-radius: 12px;
}}
QPushButton#BtnDanger:hover {{ background-color: rgba(239,68,68,0.22); }}
QPushButton#BtnDanger:pressed {{ background-color: rgba(239,68,68,0.30); }}

QPushButton#DPadBtn {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 12px;
    font-size: 18px;
    color: {C['text_mid']};
    padding: 0;
}}
QPushButton#DPadBtn:hover {{
    background-color: rgba(30,184,168,0.10);
    border-color: {C['teal']};
    color: {C['teal']};
}}
QPushButton#DPadBtn:pressed {{ background-color: rgba(30,184,168,0.20); }}
QPushButton#ZBtn {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 12px;
    font-size: 22px;
    font-weight: 700;
    color: {C['text_mid']};
    padding: 0;
}}
QPushButton#ZBtn:hover {{
    background-color: rgba(30,184,168,0.10);
    border-color: {C['teal']};
    color: {C['teal']};
}}
QPushButton#ZBtn:pressed {{ background-color: rgba(30,184,168,0.20); }}

QPushButton#StepBtn {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 10px;
    color: {C['text_dim']};
    padding: 6px 4px;
}}
QPushButton#StepBtn:hover {{ border-color: {C['teal']}; color: {C['teal']}; }}
QPushButton#StepBtnActive {{
    background-color: rgba(30,184,168,0.10);
    border: 1px solid {C['teal']};
    border-radius: 8px;
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 10px;
    color: {C['teal']};
    padding: 6px 4px;
}}

QPushButton#TbBtn {{
    background-color: transparent;
    border: none;
    border-radius: 8px;
    color: {C['text_mid']};
    font-size: 12px;
    padding: 6px 10px;
}}
QPushButton#TbBtn:hover {{
    background-color: {C['surface2']};
    color: {C['text']};
}}
QPushButton#TbBtnActive {{
    background-color: rgba(30,184,168,0.12);
    border: 1px solid rgba(30,184,168,0.30);
    border-radius: 8px;
    color: {C['teal']};
    font-size: 12px;
    padding: 6px 10px;
}}
QPushButton#TbBtnActive:hover {{ background-color: rgba(30,184,168,0.18); }}
QPushButton#TbBtnDanger {{
    background-color: transparent;
    border: none;
    border-radius: 8px;
    color: {C['red']};
    font-size: 12px;
    padding: 6px 10px;
}}
QPushButton#TbBtnDanger:hover {{ background-color: rgba(239,68,68,0.12); }}

QPushButton#PatternBtn {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    color: {C['text_dim']};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 10px;
    font-weight: 700;
    padding: 8px;
}}
QPushButton#PatternBtnActive {{
    background-color: rgba(30,184,168,0.12);
    border: 1px solid {C['teal']};
    border-radius: 10px;
    color: {C['teal']};
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    font-size: 10px;
    font-weight: 700;
    padding: 8px;
}}
QPushButton#HomeTileBtn {{
    background-color: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 20px;
    color: {C['text_mid']};
    font-size: 13px;
    font-weight: 600;
    padding: 20px;
    min-width: 160px;
    min-height: 160px;
}}
QPushButton#HomeTileBtn:hover {{
    border-color: {C['teal']};
    background-color: rgba(30,184,168,0.04);
}}
QPushButton#HomeTileBtn:pressed {{
    background-color: rgba(30,184,168,0.10);
}}
QPushButton#HomeTileBtnFeatured {{
    background-color: {C['surface']};
    border: 1px solid rgba(30,184,168,0.25);
    border-radius: 20px;
    color: {C['text_mid']};
    font-size: 13px;
    font-weight: 600;
    padding: 20px;
    min-width: 160px;
    min-height: 160px;
}}
QPushButton#HomeTileBtnFeatured:hover {{
    border-color: {C['teal']};
    background-color: rgba(30,184,168,0.08);
}}
QPushButton#HomeTileBtnLive {{
    background-color: {C['surface']};
    border: 1px solid rgba(34,197,94,0.20);
    border-radius: 20px;
    color: {C['text_mid']};
    font-size: 13px;
    font-weight: 600;
    padding: 20px;
    min-width: 160px;
    min-height: 160px;
}}
QPushButton#HomeTileBtnLive:hover {{
    border-color: {C['green']};
    background-color: rgba(34,197,94,0.06);
}}
QPushButton#SettingsNavItem {{
    background-color: transparent;
    border: none;
    border-radius: 10px;
    color: {C['text_mid']};
    font-size: 13px;
    font-weight: 500;
    padding: 10px 14px;
    text-align: left;
}}
QPushButton#SettingsNavItem:hover {{ background-color: {C['surface2']}; }}
QPushButton#SettingsNavItemActive {{
    background-color: rgba(30,184,168,0.10);
    border: none;
    border-radius: 10px;
    color: {C['teal']};
    font-size: 13px;
    font-weight: 600;
    padding: 10px 14px;
    text-align: left;
}}
QPushButton#FilterTab {{
    background-color: transparent;
    border: none;
    border-radius: 8px;
    color: {C['text_dim']};
    font-size: 12px;
    font-weight: 500;
    padding: 6px 12px;
}}
QPushButton#FilterTab:hover {{ background-color: {C['surface2']}; color: {C['text']}; }}
QPushButton#FilterTabActive {{
    background-color: {C['surface2']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    color: {C['teal']};
    font-size: 12px;
    font-weight: 600;
    padding: 6px 12px;
}}
QPushButton#ViewBtn {{
    background-color: transparent;
    border: none;
    border-radius: 6px;
    color: {C['text_dim']};
    font-size: 16px;
    padding: 4px 8px;
}}
QPushButton#ViewBtn:hover {{ background-color: {C['surface2']}; color: {C['text']}; }}
QPushButton#ViewBtnActive {{
    background-color: {C['surface2']};
    border: none;
    border-radius: 6px;
    color: {C['teal']};
    font-size: 16px;
    padding: 4px 8px;
}}
QPushButton#HomeBtn {{
    background-color: {C['teal']};
    color: #000;
    border: none;
    border-radius: 10px;
    font-weight: 700;
    font-size: 13px;
    padding: 8px 20px;
}}
QPushButton#HomeBtn:hover {{ background-color: #25d4c2; }}
QPushButton#HomeBtnDisabled {{
    background-color: {C['surface2']};
    color: {C['text_dim']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    font-weight: 700;
    font-size: 13px;
    padding: 8px 20px;
}}
QPushButton#SendBtn {{
    background-color: {C['teal']};
    color: #000;
    border: none;
    border-radius: 8px;
    font-size: 16px;
    font-weight: 700;
    padding: 6px 14px;
    min-width: 36px;
}}
QPushButton#SendBtn:hover {{ background-color: #25d4c2; }}

QProgressBar {{
    background-color: {C['surface2']};
    border: none;
    border-radius: 3px;
    text-align: center;
    font-size: 0px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {C['teal']}, stop:1 #25d4c2);
    border-radius: 3px;
}}
QCheckBox {{
    color: {C['text_mid']};
    font-size: 12px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid {C['border']};
    background: {C['surface2']};
}}
QCheckBox::indicator:checked {{
    background: {C['teal']};
    border-color: {C['teal']};
}}
"""

TEAL    = C['teal']
GREEN   = C['green']
RED     = C['red']
AMBER   = C['amber']
BORDER  = C['border']
SURFACE  = C['surface']
SURFACE2 = C['surface2']
BG       = C['bg']
TEXT     = C['text']
TEXT_DIM = C['text_dim']
TEXT_MID = C['text_mid']
WHITE    = C['white']
