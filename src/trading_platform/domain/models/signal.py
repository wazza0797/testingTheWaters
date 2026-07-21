from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SignalType(StrEnum):
    """Intent expressed by a strategy. Sizing/order-type decisions happen downstream."""

    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy's intent, published as `SignalGenerated` for the risk engine to evaluate.

    A Signal never specifies quantity or order type — that is a risk/sizing concern.
    """

    symbol: str
    signal_type: SignalType
    strategy_name: str
    timestamp: datetime
    strength: float = 1.0
    metadata: Mapping[str, Any] = field(default_factory=dict)
