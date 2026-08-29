from __future__ import annotations

import sys
from typing import TextIO


class ConsoleNotifier:
    """Writes notification messages to a text stream (stdout by default)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def notify(self, message: str, level: str = "info") -> None:
        self._stream.write(f"[{level.upper()}] {message}\n")
        self._stream.flush()
