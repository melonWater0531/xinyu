"""
Unified logger for the reCamera multimodal system.

Usage:
    from utils.logger import get_logger
    logger = get_logger(__name__)
"""

import logging
import sys
from typing import Optional


def get_logger(
    name: str,
    level: int = logging.INFO,
    fmt: Optional[str] = None,
) -> logging.Logger:
    """
    Create or retrieve a logger with a consistent format.

    Args:
        name:  Logger name (typically __name__).
        level: Logging level (default INFO).
        fmt:   Custom format string. Uses a sensible default if None.

    Returns:
        Configured logger instance.
    """
    if fmt is None:
        fmt = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"

    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
        logger.addHandler(handler)

    logger.setLevel(level)
    return logger


# Module-level default logger
_default_logger: Optional[logging.Logger] = None


def setup_root_logger(level: str = "INFO") -> logging.Logger:
    """Configure and return the root application logger."""
    global _default_logger

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    log_level = level_map.get(level.upper(), logging.INFO)

    _default_logger = get_logger("recamera_multimodal", level=log_level)
    return _default_logger
