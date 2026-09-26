"""Applies the dark theme (palette + stylesheet) to the Qt application."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from Theme.palette import Palette as P


class Theme:
    @staticmethod
    def stylesheet() -> str:
        return f"""
QWidget {{ font-family: "Segoe UI", "Inter", sans-serif; font-size: 13px; }}
QMainWindow, #Content {{ background: {P.BG_WINDOW}; }}
#Sidebar {{ background: {P.BG_SIDEBAR}; border-right: 1px solid #1f242b; }}
#Sidebar QToolButton {{ border: none; border-radius: 10px; min-width: 46px; min-height: 46px; }}
#Sidebar QToolButton:hover {{ background: #171b21; }}
#Sidebar QToolButton:checked {{ background: #1c2129; }}
#PageTitle {{ font-size: 18px; font-weight: 600; }}
#Crumb, #Muted {{ color: {P.TEXT_MUTED}; }}
#HighlightTitle {{ font-size: 17px; font-weight: 600; }}
QPushButton {{ background: {P.BG_BUTTON}; border: 1px solid {P.BORDER}; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover {{ background: {P.BG_BUTTON_HOVER}; }}
QPushButton:disabled {{ color: {P.TEXT_DISABLED}; background: #1c2027; }}
QPushButton#Primary {{ background: {P.ACCENT}; color: {P.TEXT_ON_ACCENT}; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {P.ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: {P.ACCENT_DISABLED_BG}; color: #6e7f7d; }}
QPushButton#Rec {{ background: {P.REC_BG}; border: 1px solid {P.REC_BORDER}; color: {P.REC_TEXT}; font-weight: 600; }}
QPushButton#Rec:disabled {{ background: #1c2027; border: 1px solid {P.TRACK}; color: {P.TEXT_DISABLED}; }}
QToolButton#IconBtn {{ background: transparent; border: none; padding: 4px; border-radius: 6px; }}
QToolButton#IconBtn:hover {{ background: {P.BG_HOVER}; }}
QListWidget::indicator, QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {P.TICK_MAJOR};
    border-radius: 4px; background: {P.BG_BASE}; }}
QListWidget::indicator:checked, QCheckBox::indicator:checked {{ background: {P.ACCENT}; border-color: {P.ACCENT}; }}
QLineEdit, QComboBox {{ background: {P.BG_BASE}; border: 1px solid {P.TRACK}; border-radius: 6px; padding: 5px 8px; }}
QLineEdit:focus {{ border-color: {P.ACCENT}; }}
#Card {{ background: {P.BG_CARD}; border: 1px solid #252b34; border-radius: 8px; }}
#Card:hover {{ background: {P.BG_CARD_HOVER}; }}
#Card[selected="true"] {{ background: {P.BG_CARD_SELECTED}; border: 1px solid {P.ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QListWidget, QPlainTextEdit {{ background: {P.BG_PANEL}; border: 1px solid {P.BORDER_SOFT}; border-radius: 8px; }}
QListWidget::item {{ padding: 4px; }}
QListWidget::item:selected {{ background: #232a33; color: {P.TEXT}; }}
QGroupBox {{ border: 1px solid #252b34; border-radius: 8px; margin-top: 14px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {P.ACCENT}; }}
QProgressBar {{ background: {P.BG_PANEL}; border: 1px solid {P.BORDER_SOFT}; border-radius: 5px; height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: {P.ACCENT}; border-radius: 4px; }}
QSplitter::handle {{ background: {P.BG_WINDOW}; }}
#SettingsNav {{ background: {P.BG_SIDEBAR}; border: none; border-right: 1px solid #1f242b; border-radius: 0;
    padding: 10px 8px; outline: none; }}
#SettingsNav::item {{ padding: 8px 10px; border-radius: 6px; margin: 1px 0; color: #c9d1d9; }}
#SettingsNav::item:selected {{ background: #232a33; color: {P.TEXT}; }}
#SettingsNav::item:hover:!selected {{ background: #171b21; }}
#SectionTitle {{ font-size: 18px; font-weight: 600; }}
QPushButton#Segment {{ background: {P.BG_PANEL}; border: 1px solid {P.BORDER_SOFT}; border-radius: 0; padding: 7px 0; }}
QPushButton#Segment:hover {{ background: {P.BG_BUTTON}; }}
QPushButton#Segment:checked {{ background: #2d3440; border-color: {P.ACCENT}; color: {P.TEXT}; font-weight: 600; }}
QPushButton#SegmentSmall {{ background: {P.BG_PANEL}; border: 1px solid {P.BORDER}; border-radius: 0;
    padding: 5px 12px; color: {P.TEXT_MUTED}; }}
QPushButton#SegmentSmall:hover {{ background: {P.BG_BUTTON}; color: {P.TEXT}; }}
QPushButton#SegmentSmall:checked {{ background: #1f2a2e; border-color: {P.ACCENT}; color: {P.ACCENT}; font-weight: 600; }}
#PresetCard {{ background: {P.BG_CARD}; border: 1px solid #252b34; border-radius: 8px; }}
#PresetCard:hover {{ background: {P.BG_CARD_HOVER}; }}
#PresetCard[selected="true"] {{ background: {P.BG_CARD_SELECTED}; border: 1px solid {P.ACCENT}; }}
#Banner {{ background: #1f252d; border: 1px solid #2c333d; border-radius: 8px; }}
#Banner[kind="warning"] {{ background: #2a2416; border-color: #5a4a1f; }}
#Banner[kind="ok"] {{ background: #16241c; border-color: #1f4a2e; }}
#GameTile {{ background: {P.BG_CARD}; border: 1px solid #252b34; border-radius: 8px; }}
#GameTile:hover {{ background: {P.BG_CARD_HOVER}; }}
#GameTile[planned="true"] {{ background: #15181e; }}
QToolTip {{ background: {P.BG_BUTTON}; color: {P.TEXT}; border: 1px solid {P.BORDER}; padding: 4px; }}
"""

    @classmethod
    def apply(cls, app: QApplication) -> None:
        app.setStyle("Fusion")
        pal = QPalette()
        for role, color in {
            QPalette.ColorRole.Window: P.BG_WINDOW, QPalette.ColorRole.WindowText: P.TEXT,
            QPalette.ColorRole.Base: P.BG_BASE, QPalette.ColorRole.AlternateBase: P.BG_WINDOW,
            QPalette.ColorRole.Text: P.TEXT, QPalette.ColorRole.Button: P.BG_BUTTON,
            QPalette.ColorRole.ButtonText: P.TEXT, QPalette.ColorRole.Highlight: P.ACCENT,
            QPalette.ColorRole.HighlightedText: P.TEXT_ON_ACCENT, QPalette.ColorRole.ToolTipBase: P.BG_BUTTON,
            QPalette.ColorRole.ToolTipText: P.TEXT, QPalette.ColorRole.PlaceholderText: P.TEXT_DISABLED,
        }.items():
            pal.setColor(role, QColor(color))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(P.TEXT_DISABLED))
        app.setPalette(pal)
        app.setStyleSheet(cls.stylesheet())
