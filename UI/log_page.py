"""Log page + the logging handler that feeds it from any thread."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from UI.widgets import PageHeader


class _LogSignal(QObject):
    line = pyqtSignal(str)


class QtLogHandler(logging.Handler):
    """Forwards log records to the GUI thread through a Qt signal."""

    def __init__(self) -> None:
        super().__init__()
        self.signal = _LogSignal()
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.signal.line.emit(self.format(record))
        except Exception:
            pass  # window already gone during shutdown


class LogPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(3000)
        self.view.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.addWidget(PageHeader("Log"))
        root.addWidget(self.view, 1)
        self.handler = QtLogHandler()
        self.handler.signal.line.connect(self.view.appendPlainText)

    def attach(self) -> None:
        logging.getLogger().addHandler(self.handler)

    def detach(self) -> None:
        logging.getLogger().removeHandler(self.handler)
