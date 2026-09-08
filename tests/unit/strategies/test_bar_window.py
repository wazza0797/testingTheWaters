from __future__ import annotations

import pytest

from trading_platform.strategies.bar_window import BarWindow


class TestBarWindow:
    def test_append_returns_current_contents_oldest_first(self, make_bar) -> None:
        window = BarWindow(maxlen=3)
        b1, b2 = make_bar(), make_bar()

        assert window.append(b1) == [b1]
        assert window.append(b2) == [b1, b2]

    def test_caps_at_maxlen_dropping_oldest(self, make_bar) -> None:
        window = BarWindow(maxlen=2)
        b1, b2, b3 = make_bar(), make_bar(), make_bar()

        window.append(b1)
        window.append(b2)
        result = window.append(b3)

        assert result == [b2, b3]

    def test_len_reflects_current_size(self, make_bar) -> None:
        window = BarWindow(maxlen=5)
        assert len(window) == 0
        window.append(make_bar())
        assert len(window) == 1

    def test_clear_empties_the_window(self, make_bar) -> None:
        window = BarWindow(maxlen=3)
        window.append(make_bar())
        window.clear()
        assert len(window) == 0

    def test_maxlen_property(self) -> None:
        window = BarWindow(maxlen=10)
        assert window.maxlen == 10

    def test_rejects_maxlen_less_than_two(self) -> None:
        with pytest.raises(ValueError, match="maxlen"):
            BarWindow(maxlen=1)
