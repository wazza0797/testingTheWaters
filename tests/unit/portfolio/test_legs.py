from __future__ import annotations

from decimal import Decimal

import pytest

from trading_platform.portfolio.legs import LegBook


def test_leg_book_add_and_reduce() -> None:
    book = LegBook()
    book.apply_fill("SYM", leg="core", side="buy", filled_qty=Decimal("10"))
    book.apply_fill("SYM", leg="tilt", side="buy", filled_qty=Decimal("2"))
    assert book.qty("SYM", "core") == Decimal("10")
    assert book.qty("SYM", "tilt") == Decimal("2")
    assert book.net("SYM") == Decimal("12")
    book.apply_fill("SYM", leg="tilt", side="sell", filled_qty=Decimal("2"))
    assert book.qty("SYM", "tilt") == Decimal("0")
    assert book.qty("SYM", "core") == Decimal("10")


def test_leg_book_rejects_negative() -> None:
    book = LegBook()
    with pytest.raises(ValueError):
        book.set_qty("SYM", "tilt", Decimal("-1"))
