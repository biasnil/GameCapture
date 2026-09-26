"""Console + rotating file logging."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class LogSetup:
    FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
    DATE = "%Y-%m-%d %H:%M:%S"

    @classmethod
    def configure(cls, log_dir: Path, level: str = "INFO") -> Path:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "gamecapture.log"
        fmt = logging.Formatter(cls.FORMAT, cls.DATE)

        file = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        file.setFormatter(fmt)
        handlers: list[logging.Handler] = [file]
        if sys.stdout is not None:  # pythonw (GameCapture.pyw) has no console
            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(fmt)
            handlers.append(console)

        root = logging.getLogger()
        root.handlers = handlers
        root.setLevel(level.upper())
        return log_file
