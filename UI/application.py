"""Creates the Qt application, applies the theme and shows the main window."""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from Core.config import AppConfig
from Theme.theme import Theme
from UI.icons import Icons
from UI.main_window import MainWindow


class GameCaptureApp:
    APP_ID = "GameCapture"

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    @classmethod
    def _set_windows_app_id(cls) -> None:
        """Own taskbar entry and icon instead of python.exe's."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(cls.APP_ID)
        except Exception:
            pass

    def run(self) -> int:
        self._set_windows_app_id()
        app = QApplication(sys.argv)
        app.setApplicationName(self.APP_ID)
        app.setWindowIcon(Icons.app_icon())
        app.setQuitOnLastWindowClosed(False)  # the tray keeps it alive
        Theme.apply(app)
        window = MainWindow(self.cfg)
        if not (self.cfg.gui.start_minimized and window.tray):
            window.show()
        return app.exec()
