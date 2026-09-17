"""Logging: a rotating file for forensics, a quiet console for the operator."""
from __future__ import annotations

import logging
import logging.handlers

from jarvis.config import USER_DIR

LOG_PATH = USER_DIR / "jarvis.log"


def setup(level: str = "INFO", console: bool = True) -> None:
    root = logging.getLogger()
    if root.handlers:  # idempotent: tests import this repeatedly
        return
    root.setLevel(logging.DEBUG)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)-20s %(message)s")
    )
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(getattr(logging, level.upper(), logging.INFO))
        stream.setFormatter(logging.Formatter("%(levelname)-7s %(name)-16s %(message)s"))
        root.addHandler(stream)

    # These libraries log per-frame at DEBUG and drown everything else.
    for noisy in ("comtypes", "PIL", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
