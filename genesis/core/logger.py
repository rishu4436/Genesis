"""Structured logging setup with loguru."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "data" / "logs"


def setup_logger(
    level: str = "INFO",
    log_file: bool = True,
    json_logs: bool = False,
) -> None:
    """Configure loguru for CLI and agent use."""
    logger.remove()

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    if json_logs:
        fmt = "{message}"

    logger.add(sys.stderr, level=level, format=fmt, colorize=not json_logs)

    if log_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger.add(
            LOG_DIR / "genesis_{time:YYYY-MM-DD}.log",
            rotation="1 day",
            retention="30 days",
            level=level,
            serialize=json_logs,
        )


def get_logger(name: str | None = None):
    """Return bound logger instance."""
    if name:
        return logger.bind(component=name)
    return logger