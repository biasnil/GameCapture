"""Talking to the operating system: open files/folders, reveal in Explorer, clipboard."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication


class Shell:
    @staticmethod
    def open_path(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @classmethod
    def reveal(cls, path: Path) -> None:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            cls.open_path(path.parent)

    @staticmethod
    def copy_file_to_clipboard(path: Path) -> None:
        """Puts the file itself on the clipboard: paste into Discord, Explorer, etc."""
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        QApplication.clipboard().setMimeData(mime)
