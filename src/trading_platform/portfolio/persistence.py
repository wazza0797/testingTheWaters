from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import OrderSide
from trading_platform.domain.models.position import Position
from trading_platform.portfolio.book import PortfolioBook
from trading_platform.utils.time import to_utc

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PaperStateSnapshot:
    """Serializable paper-session state (cash, positions, last processed bar)."""

    cash: Decimal
    positions: dict[str, Position]
    last_bar_timestamp: datetime | None
    fills: tuple[Fill, ...]


class JsonPaperStateStore:
    """Load/save `PaperStateSnapshot` as JSON under a single file path."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def load(self) -> PaperStateSnapshot | None:
        if not self.exists():
            return None
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return _snapshot_from_dict(raw)

    def save(self, snapshot: PaperStateSnapshot) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = _snapshot_to_dict(snapshot)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)
        logger.debug("paper_state_saved", extra={"path": str(self._path)})


def snapshot_from_book(
    book: PortfolioBook,
    *,
    last_bar_timestamp: datetime | None,
) -> PaperStateSnapshot:
    return PaperStateSnapshot(
        cash=book.cash,
        positions=dict(book.positions),
        last_bar_timestamp=last_bar_timestamp,
        fills=book.fills,
    )


def book_from_snapshot(snapshot: PaperStateSnapshot) -> PortfolioBook:
    return PortfolioBook.from_snapshot(
        snapshot.cash,
        snapshot.positions,
        timestamp=snapshot.last_bar_timestamp,
        fills=list(snapshot.fills),
    )


def _snapshot_to_dict(snapshot: PaperStateSnapshot) -> dict[str, Any]:
    return {
        "cash": str(snapshot.cash),
        "last_bar_timestamp": (
            snapshot.last_bar_timestamp.isoformat() if snapshot.last_bar_timestamp else None
        ),
        "positions": {
            symbol: {
                "quantity": str(pos.quantity),
                "average_entry_price": str(pos.average_entry_price),
                "realized_pnl": str(pos.realized_pnl),
            }
            for symbol, pos in snapshot.positions.items()
        },
        "fills": [_fill_to_dict(f) for f in snapshot.fills],
    }


def _snapshot_from_dict(raw: dict[str, Any]) -> PaperStateSnapshot:
    positions: dict[str, Position] = {}
    for symbol, pos_raw in (raw.get("positions") or {}).items():
        positions[symbol] = Position(
            symbol=symbol,
            quantity=Decimal(str(pos_raw["quantity"])),
            average_entry_price=Decimal(str(pos_raw["average_entry_price"])),
            realized_pnl=Decimal(str(pos_raw.get("realized_pnl", "0"))),
        )
    fills = tuple(_fill_from_dict(f) for f in (raw.get("fills") or []))
    last_raw = raw.get("last_bar_timestamp")
    last_ts = to_utc(datetime.fromisoformat(last_raw)) if last_raw else None
    return PaperStateSnapshot(
        cash=Decimal(str(raw["cash"])),
        positions=positions,
        last_bar_timestamp=last_ts,
        fills=fills,
    )


def _fill_to_dict(fill: Fill) -> dict[str, Any]:
    return {
        "order_id": fill.order_id,
        "correlation_id": fill.correlation_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "filled_qty": str(fill.filled_qty),
        "remaining_qty": str(fill.remaining_qty),
        "fill_price": str(fill.fill_price),
        "fee": str(fill.fee),
        "fee_type": fill.fee_type.value,
        "is_complete": fill.is_complete,
        "timestamp": fill.timestamp.isoformat(),
    }


def _fill_from_dict(raw: dict[str, Any]) -> Fill:
    return Fill(
        order_id=str(raw["order_id"]),
        correlation_id=str(raw["correlation_id"]),
        symbol=str(raw["symbol"]),
        side=OrderSide(raw["side"]),
        filled_qty=Decimal(str(raw["filled_qty"])),
        remaining_qty=Decimal(str(raw["remaining_qty"])),
        fill_price=Decimal(str(raw["fill_price"])),
        fee=Decimal(str(raw["fee"])),
        fee_type=FeeType(raw["fee_type"]),
        is_complete=bool(raw["is_complete"]),
        timestamp=to_utc(datetime.fromisoformat(str(raw["timestamp"]))),
    )
