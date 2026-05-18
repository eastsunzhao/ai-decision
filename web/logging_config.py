"""Central logging setup for the Web runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger


def configure_logging(root_dir: Path | None = None) -> Path:
    """Configure loguru sinks for the Web app.

    Logs are written to ``log/app.log`` by default, rotated daily, compressed,
    and retained for 30 days. The path can be overridden with ``APP_LOG_FILE``.
    """
    project_root = root_dir or Path(__file__).resolve().parents[1]
    default_log_file = project_root / "log" / "app.log"
    log_file = Path(os.environ.get("APP_LOG_FILE", str(default_log_file))).expanduser()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = os.environ.get("APP_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    rotation = os.environ.get("APP_LOG_ROTATION", "00:00")
    retention = os.environ.get("APP_LOG_RETENTION", "30 days")
    compression = os.environ.get("APP_LOG_COMPRESSION", "zip")

    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        colorize=False,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    logger.add(
        str(log_file),
        level=level,
        rotation=rotation,
        retention=retention,
        compression=compression,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    logger.enable("nanobot")
    logger.enable("web")
    logger.info(
        "Logging configured: file={}, level={}, rotation={}, retention={}, compression={}",
        log_file,
        level,
        rotation,
        retention,
        compression,
    )
    return log_file
