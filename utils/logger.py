"""Logging Configuration"""
import logging
import logging.handlers
from pathlib import Path
from core_logic.config import Config

def setup_logging(log_file=None, level=None) -> logging.Logger:
    """Set up logging for D.A.E.M.O.N.

    Attaches handlers to the *root* logger so that all module-level loggers
    obtained via ``logging.getLogger(__name__)`` inherit them automatically.
    """
    if log_file is None:
        log_file = Config.LOGS_DIR / "daemon.log"
    else:
        log_file = Path(log_file)

    if level is None:
        level = getattr(Config, "LOG_LEVEL", "INFO") or "INFO"

    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Only install handlers once — re-calls of setup_logging() are harmless.
    already_installed = any(
        getattr(h, "_daemon_handler", False) for h in root.handlers
    )
    if not already_installed:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        console_handler._daemon_handler = True  # type: ignore[attr-defined]
        root.addHandler(console_handler)

        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler._daemon_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    # Quiet down noisy third-party loggers a bit.
    for noisy in ("urllib3", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel("WARNING")

    return logging.getLogger("daemon")
