import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

APP_NAME = "OVLS Linux Support"
VERSION = "0.1.0"


def setup_logging(log_path):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    return logging.getLogger("ovls")


def print_status(status):
    print(json.dumps(status, indent=2, ensure_ascii=False))


def now():
    return datetime.now().isoformat()