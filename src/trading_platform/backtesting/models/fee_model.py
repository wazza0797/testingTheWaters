from __future__ import annotations

from decimal import Decimal

from trading_platform.domain.models.fill import FeeType
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import OrderType


class FeeModel:
    """Determines maker vs taker fee type, and the fee amount, for a fill.

    - **Market orders are always taker** — a market order removes liquidity
      by definition.
    - **Limit orders** depend on whether they cross the spread on
      submission: a crossing limit (aggressive — effectively takes liquidity
      immediately) is taker; a non-crossing limit that rests and is later hit
      is maker, if `assume_maker_on_limit` is configured (the OHLCV-only
      approximation this whole backtest engine is built on can't observe a
      real order book to know for certain — see `docs/architecture.md`).
    """

    def __init__(self, assume_maker_on_limit: bool) -> None:
        self._assume_maker_on_limit = assume_maker_on_limit

    def fee_type_for(self, order_type: OrderType, *, crosses_on_submission: bool) -> FeeType:
        if order_type == OrderType.MARKET:
            return FeeType.TAKER
        if crosses_on_submission:
            return FeeType.TAKER
        return FeeType.MAKER if self._assume_maker_on_limit else FeeType.TAKER

    @staticmethod
    def calculate_fee(
        filled_qty: Decimal, fill_price: Decimal, fee_type: FeeType, rules: InstrumentRules
    ) -> Decimal:
        rate = rules.maker_fee_rate if fee_type == FeeType.MAKER else rules.taker_fee_rate
        return filled_qty * fill_price * rate
