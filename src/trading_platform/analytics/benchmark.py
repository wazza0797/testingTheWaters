from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.models.bar import Bar

_HUNDRED = Decimal("100")


def buy_and_hold_return_pct(
    bars: Sequence[Bar],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Decimal | None:
    """Percentage return from buying at the first close in range and holding
    to the last close. `end` is exclusive when provided (same convention as
    CLI `--end` / hold-out windows).

    Returns `None` when fewer than two bars fall in the range.
    """
    in_range = [
        bar
        for bar in bars
        if (start is None or bar.timestamp >= start) and (end is None or bar.timestamp < end)
    ]
    if len(in_range) < 2:
        return None
    first = in_range[0].close
    last = in_range[-1].close
    if first == 0:
        return None
    return (last - first) / first * _HUNDRED
