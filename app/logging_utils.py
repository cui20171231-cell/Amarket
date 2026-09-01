from __future__ import annotations

import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

MIB = 1024 * 1024

_append_lock = threading.Lock()


def bounded_log_handler(
    path: Path,
    *,
    max_bytes: int = 20 * MIB,
    backup_count: int = 50,
) -> RotatingFileHandler:
    """Return a UTF-8 file handler with a hard size ceiling."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )


def append_jsonl_bounded(
    path: Path,
    record: dict[str, Any],
    *,
    max_bytes: int = 20 * MIB,
    backup_count: int = 25,
) -> None:
    """Append one JSON line and keep the complete audit family size-bounded."""
    encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _append_lock:
        if path.exists() and path.stat().st_size + len(encoded) > max_bytes:
            oldest = path.with_name(f"{path.name}.{backup_count}")
            oldest.unlink(missing_ok=True)
            for index in range(backup_count - 1, 0, -1):
                source = path.with_name(f"{path.name}.{index}")
                if source.exists():
                    os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
            os.replace(path, path.with_name(f"{path.name}.1"))
        with path.open("ab") as file:
            file.write(encoded)


def configure_bounded_root_logging(
    path: Path,
    *,
    max_bytes: int = 20 * MIB,
    backup_count: int = 50,
) -> list[logging.Handler]:
    """Build console plus bounded-file handlers for long-running commands."""
    return [
        logging.StreamHandler(),
        bounded_log_handler(
            path,
            max_bytes=max_bytes,
            backup_count=backup_count,
        ),
    ]
