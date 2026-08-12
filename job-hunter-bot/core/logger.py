"""Configuración de logging: archivo rotativo (5 MB x 3) + consola."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

_CONFIGURED = False


def setup_logger(
    log_file: str = "logs/bot.log",
    level: str = "INFO",
    name: str = "job_hunter",
) -> logging.Logger:
    """Crea (una sola vez) el logger raíz del bot.

    Args:
        log_file: ruta del archivo de log (se crea el directorio si falta).
        level: nivel de logging (DEBUG/INFO/WARNING/ERROR).
        name: nombre del logger.

    Returns:
        Logger listo para usar.
    """
    global _CONFIGURED
    logger = logging.getLogger(name)
    if _CONFIGURED:
        return logger

    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    directory = os.path.dirname(os.path.abspath(log_file))
    if directory:
        os.makedirs(directory, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Silenciar ruido de librerías
    for noisy in ("urllib3", "apscheduler", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Devuelve un logger hijo del logger principal."""
    base = "job_hunter"
    return logging.getLogger(f"{base}.{name}" if name else base)
