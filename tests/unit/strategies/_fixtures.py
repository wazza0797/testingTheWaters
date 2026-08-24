"""Deliberately malformed strategy classes, importable by dotted path, used
only to test `strategies/loader.py`'s runtime `IStrategy` shape check. Not
prefixed `test_` so pytest never collects this file as a test module itself.
"""

from __future__ import annotations


class MissingOnStopStrategy:
    """Missing `on_stop` — otherwise looks exactly like a real strategy."""

    def on_start(self, ctx: object) -> None:
        pass

    def on_bar(self, bar: object, ctx: object) -> list[object]:
        return []
