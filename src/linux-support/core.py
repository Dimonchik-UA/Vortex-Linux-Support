import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path

APP_NAME = "OVLS Linux Support"
VERSION = "0.2.0"

# Log rotation constants
LOG_MAX_BYTES = 1_000_000  # 1 MB
LOG_BACKUP_COUNT = 2       # 2 backups = ~3 MB total


def _make_rotating_handler(log_path: str):
    """Create a RotatingFileHandler with standard OVLS settings."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    return handler


def setup_logging(log_path: str):
    """Configure root logger with a rotating file handler.

    Idempotent: safe to call multiple times; does not add duplicate handlers.
    """
    abs_path = os.path.abspath(log_path)
    handler = _make_rotating_handler(abs_path)

    root_logger = logging.getLogger()
    # Remove any existing file handlers for the same path to avoid duplicates
    for h in list(root_logger.handlers):
        if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == abs_path:
            root_logger.removeHandler(h)
            h.close()

    # Also check if handler is already attached (by identity)
    if handler not in root_logger.handlers:
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    return logging.getLogger("ovls")


def print_status(status):
    print(json.dumps(status, indent=2, ensure_ascii=False))


def now():
    return datetime.now().isoformat()