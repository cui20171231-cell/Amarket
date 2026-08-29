from __future__ import annotations

import msvcrt
from pathlib import Path
from typing import BinaryIO, Self


class SingleInstanceLock:
    """Hold one Windows file lock for the lifetime of the collector process."""

    def __init__(self, path: Path):
        self.path = path
        self._stream: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        # LK_LOCK waits instead of exiting. If an earlier collector is still
        # shutting down, the scheduled task remains alive and takes over as
        # soon as that process releases the lock.
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        self._stream = stream

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.seek(0)
            msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
