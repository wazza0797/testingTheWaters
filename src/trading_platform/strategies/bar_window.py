from __future__ import annotations

from collections import deque

from trading_platform.domain.models.bar import Bar


class BarWindow:
    """A bounded rolling buffer of the most recent `Bar`s, capped at
    `maxlen` — the same "strategy keeps its own bar history" pattern every
    strategy in this package uses (`on_bar` only ever receives one new bar
    at a time), factored out so `RuleStrategy`/`RegimeRouterStrategy` (and
    any future strategy) don't each hand-roll a `deque[Bar]`.

    `maxlen` should cover the longest indicator period referenced anywhere
    in a strategy's condition trees — see each params' `lookback` field. A
    window smaller than that just means the relevant indicator(s) return
    `NaN` forever (safe, never raises — see `strategies/rules/evaluator.py`'s
    3-valued logic), not a crash, so undersizing `lookback` is a silent
    "always warming up", not a bug to guard against here.
    """

    def __init__(self, maxlen: int) -> None:
        if maxlen < 2:
            raise ValueError(
                f"maxlen must be >= 2 (need at least 2 bars for cross detection), got {maxlen}"
            )
        self._maxlen = maxlen
        self._bars: deque[Bar] = deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def append(self, bar: Bar) -> list[Bar]:
        """Append `bar` and return the current window contents (oldest
        first) as a plain `list` — the shape every indicator/condition
        helper in this package expects (`Sequence[Bar]`)."""
        self._bars.append(bar)
        return list(self._bars)

    def clear(self) -> None:
        self._bars.clear()

    def __len__(self) -> int:
        return len(self._bars)
