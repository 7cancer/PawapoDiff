"""Local rotating diagnostics. Never log uploaded slide text or authentication tokens."""

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
import tempfile
import time


class ConsoleHandler(logging.StreamHandler):
    # Resolve stderr at emit time so redirected consoles/capture streams work.
    _pawapo_console = True

    def emit(self, record):
        self.stream = sys.stderr
        if self.stream is not None:
            super().emit(record)


def configure_diagnostics(*, console=False):
    logger = logging.getLogger("pawapodiff")
    logger.setLevel(logging.INFO)
    if console and not any(
        getattr(h, "_pawapo_console", False) for h in logger.handlers
    ):
        handler = ConsoleHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s"
            )
        )
        logger.addHandler(handler)
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            return Path(handler.baseFilename)
    roots = []
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "PawapoDiff" / "logs")
    roots.append(Path(tempfile.gettempdir()) / "PawapoDiff" / "logs")
    for root in roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / f"pawapodiff-{os.getpid()}.log"
            handler = RotatingFileHandler(
                path, maxBytes=2 * 1024**2, backupCount=2, encoding="utf-8"
            )
        except OSError:
            continue
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(process)d %(levelname)s %(name)s %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # Separate files avoid cross-process rotation/file-lock failures on Windows.
        for old in root.glob("pawapodiff-*.log*"):
            try:
                if old != path and old.stat().st_mtime < time.time() - 7 * 86400:
                    old.unlink()
            except OSError:
                pass  # An active process may still hold the log open.
        return path
    logger.warning("No writable diagnostic log directory is available")
    return None


def diagnostic_files():
    path = configure_diagnostics()
    if path is None:
        return []
    files = []
    for candidate in path.parent.glob("pawapodiff-*.log*"):
        try:
            files.append((candidate.stat().st_mtime, candidate))
        except OSError:
            pass
    return [entry[1] for entry in sorted(files, reverse=True)[:20]]
