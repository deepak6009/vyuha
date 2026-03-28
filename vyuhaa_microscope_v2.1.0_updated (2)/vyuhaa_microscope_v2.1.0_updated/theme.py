"""
theme.py — Vyuhaa Microscope UI colours and shared stylesheet fragments.
"""

# ── Palette ───────────────────────────────────────────────────────────────────
BG          = "#0d1117"
SURFACE     = "#161b22"
SURFACE2    = "#1c2330"
BORDER      = "#2a3441"
TEAL        = "#1eb8a8"
TEAL_DIM    = "rgba(30,184,168,0.10)"
RED         = "#ef4444"
GREEN       = "#22c55e"
TEXT        = "#e2e8f0"
TEXT_DIM    = "#64748b"
TEXT_MID    = "#94a3b8"
WHITE       = "#ffffff"
BAR_HEIGHT  = 56
LOGO_SIZE   = 32
SURFACE2    = "#21262d"
TILE_GAP    = 20
TILE_SIZE   = 140
TILE_W      = 140
TILE_H      = 140
ACTION_SIZE = 80

# ── Global QSS ───────────────────────────────────────────────────────────────
GLOBAL_QSS = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "DM Sans", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 4px;
    border-radius: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 2px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""

# ── Reusable button styles ────────────────────────────────────────────────────
BTN_PRIMARY = f"""
QPushButton {{
    background-color: {TEAL};
    color: #000000;
    border: none;
    border-radius: 10px;
    padding: 12px 20px;
    font-weight: 700;
    font-size: 13px;
}}
QPushButton:hover {{ background-color: #25d4c2; }}
QPushButton:pressed {{ background-color: #18a090; }}
QPushButton:disabled {{ opacity: 0.5; }}
"""

BTN_SECONDARY = f"""
QPushButton {{
    background-color: {SURFACE2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 12px 20px;
    font-size: 13px;
}}
QPushButton:hover {{ border-color: {TEAL}; color: {TEAL}; }}
QPushButton:pressed {{ background-color: {SURFACE}; }}
"""

BTN_DANGER = f"""
QPushButton {{
    background-color: rgba(239,68,68,0.12);
    color: {RED};
    border: 1px solid rgba(239,68,68,0.25);
    border-radius: 10px;
    padding: 12px 20px;
    font-size: 13px;
}}
QPushButton:hover {{ background-color: rgba(239,68,68,0.22); }}
"""

INPUT_FIELD = f"""
QLineEdit, QTextEdit {{
    background-color: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 11px 14px;
    color: {TEXT};
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
}}
QLineEdit:focus, QTextEdit:focus {{ border-color: {TEAL}; }}
"""

LABEL_FIELD = f"""
QLabel {{
    font-size: 10px;
    color: {TEXT_DIM};
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    background: transparent;
}}
"""
