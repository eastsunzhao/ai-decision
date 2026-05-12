from __future__ import annotations

import gzip
import logging
import shutil
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class GzipTimedRotatingFileHandler(TimedRotatingFileHandler):
    def rotate(self, source: str, dest: str) -> None:
        source_path = Path(source)
        dest_path = Path(dest)
        if not source_path.exists():
            return
        with source_path.open("rb") as source_file:
            with gzip.open(f"{dest_path}.gz", "wb") as gz_file:
                shutil.copyfileobj(source_file, gz_file)
        source_path.unlink()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def setup_logging() -> None:
    root_logger = logging.getLogger()
    if getattr(root_logger, "_ai_decision_logging_configured", False):
        return

    log_dir = project_root() / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = GzipTimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=60,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger._ai_decision_logging_configured = True

    logging.getLogger("ai_decision").info("logging_initialized log_file=%s retention_days=60 compression=gzip", log_file)
