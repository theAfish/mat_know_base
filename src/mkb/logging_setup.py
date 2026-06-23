"""Centralised logging configuration for MKB.

Two modes are supported, selected via ``MKB_LOG_LEVEL`` (or
``settings.log_level``):

* ``DEBUG`` — verbose console output; intended for debugging. Agent
  dialogs / tool calls / function usage, MinerU's full loguru output,
  and ``litellm``/``google_adk``/``httpx`` traces all appear.
* ``INFO`` — concise console output. Noisy third-party libraries are
  clamped to WARNING; MinerU's loguru sink is suppressed except for
  warnings/errors.

In both modes ``mkb.log`` always records the full DEBUG stream on disk
(rotating), so post-mortem inspection is always available.
MinerU / PDF processor output (both local loguru and API stdlib) is
additionally written to ``mineru.log``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_INITIALISED = False

# Third-party loggers that are extremely chatty at DEBUG. Always clamp
# them in INFO mode; in DEBUG mode they stay at DEBUG so traces survive
# inside the rolling debug file.
_NOISY_LIBS = (
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
    "botocore",
    "boto3",
    "s3transfer",
    "matplotlib",
    "PIL",
    "watchfiles",
    "uvicorn.access",
)

# Loggers that we always want to surface from our own code.
_APP_LOGGERS = ("mkb",)


def _coerce_level(value: str | int | None, default: int = logging.DEBUG) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    val = str(value).strip().upper()
    return logging.getLevelName(val) if val in logging._nameToLevel else default


def setup_logging(
    level: str | int | None = None,
    log_dir: str | os.PathLike[str] | None = None,
    *,
    force: bool = False,
) -> Path:
    """Configure root logging. Safe to call multiple times.

    Returns the directory where log files are being written.
    """
    global _INITIALISED
    if _INITIALISED and not force:
        return Path(log_dir) if log_dir else _current_log_dir()

    # Resolve settings lazily so this module has no import-time deps on
    # config (which itself reads .env).
    from mkb.config import settings

    if level is None:
        try:
            from mkb.runtime_settings import get_setting
            configured_level = get_setting("log_level")
        except Exception:
            configured_level = settings.log_level
    else:
        configured_level = level
    resolved_level = _coerce_level(configured_level, logging.DEBUG)
    resolved_dir = Path(log_dir or settings.log_dir).resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)

    is_debug = resolved_level <= logging.DEBUG

    console_fmt = (
        "%(asctime)s %(levelname)-5s %(name)s [%(threadName)s] %(message)s"
        if is_debug
        else "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    )
    file_fmt = (
        "%(asctime)s %(levelname)-5s %(name)s [%(threadName)s] "
        "%(filename)s:%(lineno)d - %(message)s"
    )

    root = logging.getLogger()
    # Reset handlers — many third-party imports (uvicorn, mineru, etc.)
    # call ``basicConfig`` and leave handlers behind.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter.

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(resolved_level)
    console.setFormatter(logging.Formatter(console_fmt))
    root.addHandler(console)

    # Single rolling file always captures the full DEBUG stream; the
    # console handler applies the user-selected level for on-screen output.
    max_bytes = max(1, int(settings.log_file_max_mb)) * 1024 * 1024
    backups = max(0, int(settings.log_file_backup_count))
    # RotatingFileHandler forces mode='a' when maxBytes>0, so truncate
    # the files explicitly here so each startup begins with a clean slate.
    for _fname in ("mkb.log", "mineru.log"):
        try:
            (resolved_dir / _fname).open("w").close()
        except OSError:
            pass
    main_file = logging.handlers.RotatingFileHandler(
        resolved_dir / "mkb.log",
        maxBytes=max_bytes,
        backupCount=backups,
        encoding="utf-8",
    )
    main_file.setLevel(logging.DEBUG)
    main_file.setFormatter(logging.Formatter(file_fmt))
    root.addHandler(main_file)

    # Application loggers — explicit level so child loggers behave.
    for name in _APP_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG if is_debug else logging.INFO)

    # Tame noisy third-party loggers in INFO mode; in DEBUG let them
    # stream so the debug file captures everything.
    for name in _NOISY_LIBS:
        logging.getLogger(name).setLevel(logging.DEBUG if is_debug else logging.WARNING)

    # google-adk / litellm / mineru: in DEBUG show everything, in INFO
    # surface only warnings.
    for name in ("google_adk", "google.adk", "mineru"):
        logging.getLogger(name).setLevel(logging.DEBUG if is_debug else logging.WARNING)
    # LiteLLM DEBUG formats and logs entire prompts/tool histories before the
    # HTTP call. Keep useful lifecycle logs without serializing huge requests.
    for name in ("litellm", "LiteLLM"):
        logging.getLogger(name).setLevel(logging.INFO if is_debug else logging.WARNING)

    # Dedicated mineru.log that captures BOTH loguru (local backend) and
    # stdlib (API backend) PDF processing logs.
    mineru_handler = logging.handlers.RotatingFileHandler(
        resolved_dir / "mineru.log",
        maxBytes=max_bytes,
        backupCount=backups,
        encoding="utf-8",
    )
    mineru_handler.setLevel(logging.DEBUG if is_debug else logging.WARNING)
    mineru_handler.setFormatter(logging.Formatter(file_fmt))
    # mkb.processors covers the MinerU API backend logger.
    logging.getLogger("mkb.processors").addHandler(mineru_handler)

    _configure_loguru(resolved_dir, is_debug, mineru_handler)

    os.environ["MKB_LOG_DIR"] = str(resolved_dir)
    os.environ["MKB_LOG_LEVEL_EFFECTIVE"] = logging.getLevelName(resolved_level)

    _INITIALISED = True
    logging.getLogger("mkb.logging").info(
        "Logging initialised (level=%s, dir=%s)",
        logging.getLevelName(resolved_level),
        resolved_dir,
    )
    return resolved_dir


def _current_log_dir() -> Path:
    from mkb.config import settings
    return Path(settings.log_dir).resolve()


def _configure_loguru(
    log_dir: Path,
    is_debug: bool,
    file_handler: logging.handlers.RotatingFileHandler | None = None,
) -> None:
    """Bridge loguru (used by MinerU local backend & friends) into our handlers.

    * In DEBUG: keep loguru's default stderr sink, plus writes to the shared
      ``mineru.log`` RotatingFileHandler (same file as the stdlib processors
      logger so both paths land in one place).
    * In INFO: drop loguru's stderr sink and only persist warnings/errors
      (forwarded to stdlib so they hit the console + files pipeline too).
    """
    try:
        from loguru import logger as _loguru
    except Exception:
        return

    _loguru.remove()

    fmt = (
        "{time:YYYY-MM-DD HH:mm:ss} {level: <5} {name}:{function}:{line} - {message}"
    )

    if is_debug:
        _loguru.add(sys.stderr, level="DEBUG", format=fmt, enqueue=True)
        if file_handler is not None:
            # Write loguru records through the shared RotatingFileHandler so
            # both the local-backend loguru output and the API-backend stdlib
            # output are interleaved in the same mineru.log file.
            class _HandlerSink:
                def write(self, message):
                    record = message.record
                    level_name = record["level"].name
                    stdlib_level = getattr(logging, level_name, logging.DEBUG)
                    lr = logging.LogRecord(
                        name=record["name"],
                        level=stdlib_level,
                        pathname=record["file"].path,
                        lineno=record["line"],
                        msg=record["message"].rstrip(),
                        args=(),
                        exc_info=None,
                    )
                    file_handler.emit(lr)

                def flush(self):
                    file_handler.flush()

            _loguru.add(_HandlerSink(), level="DEBUG", format="{message}", enqueue=True)
        else:
            _loguru.add(
                log_dir / "mineru.log",
                level="DEBUG",
                format=fmt,
                rotation="20 MB",
                retention=5,
                enqueue=True,
            )
    else:
        # Forward warnings/errors to stdlib so they hit the same console
        # + file pipeline as the rest of the app.
        class _StdlibSink:
            def write(self, message):
                record = message.record
                level_name = record["level"].name
                stdlib_level = getattr(logging, level_name, logging.WARNING)
                logging.getLogger("mineru").log(
                    stdlib_level,
                    record["message"].rstrip(),
                )

            def flush(self):
                pass

        _loguru.add(_StdlibSink(), level="WARNING", format="{message}")
        if file_handler is not None:
            class _WarnHandlerSink:
                def write(self, message):
                    record = message.record
                    level_name = record["level"].name
                    stdlib_level = getattr(logging, level_name, logging.WARNING)
                    lr = logging.LogRecord(
                        name=record["name"],
                        level=stdlib_level,
                        pathname=record["file"].path,
                        lineno=record["line"],
                        msg=record["message"].rstrip(),
                        args=(),
                        exc_info=None,
                    )
                    file_handler.emit(lr)

                def flush(self):
                    file_handler.flush()

            _loguru.add(_WarnHandlerSink(), level="WARNING", format="{message}")


def is_debug_mode() -> bool:
    """True if the active log level is DEBUG or lower."""
    return logging.getLogger().getEffectiveLevel() <= logging.DEBUG


def get_log_dir() -> Path:
    """Return the directory where log files are written."""
    return _current_log_dir()
