"""
pages/manual_move_page.py
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

# ...existing code...
