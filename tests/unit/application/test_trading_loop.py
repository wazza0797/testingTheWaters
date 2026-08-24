from __future__ import annotations

from trading_platform.application.trading_loop import TradingLoop
from trading_platform.domain.events.market import BarClosed


class TestTradingLoop:
    def test_publishes_one_bar_closed_per_bar_in_order(self, fake_event_bus, make_bar) -> None:
        loop = TradingLoop(fake_event_bus, mode="backtest")
        bars = [
            make_bar(open_=str(100 + i), high=str(110 + i), low=str(90 + i), close=str(105 + i))
            for i in range(3)
        ]

        loop.run(bars)

        published = [e for e in fake_event_bus.published if isinstance(e, BarClosed)]
        assert [e.bar for e in published] == bars

    def test_published_bar_closed_events_use_the_configured_mode(
        self, fake_event_bus, make_bar
    ) -> None:
        loop = TradingLoop(fake_event_bus, mode="paper")

        loop.run([make_bar()])

        published = fake_event_bus.published[0]
        assert isinstance(published, BarClosed)
        assert published.mode == "paper"

    def test_returns_the_number_of_bars_processed(self, fake_event_bus, make_bar) -> None:
        loop = TradingLoop(fake_event_bus, mode="backtest")

        count = loop.run([make_bar(), make_bar(), make_bar()])

        assert count == 3

    def test_empty_bar_source_publishes_nothing_and_returns_zero(self, fake_event_bus) -> None:
        loop = TradingLoop(fake_event_bus, mode="backtest")

        count = loop.run([])

        assert count == 0
        assert fake_event_bus.published == []

    def test_before_bar_hook_runs_before_bar_closed_is_published(
        self, fake_event_bus, make_bar
    ) -> None:
        loop = TradingLoop(fake_event_bus, mode="backtest")
        published_count_when_before_bar_ran = []

        loop.run(
            [make_bar()],
            before_bar=lambda bar: published_count_when_before_bar_ran.append(
                len(fake_event_bus.published)
            ),
        )

        assert published_count_when_before_bar_ran == [0]
        assert len(fake_event_bus.published) == 1

    def test_after_bar_hook_runs_after_bar_closed_is_published(
        self, fake_event_bus, make_bar
    ) -> None:
        loop = TradingLoop(fake_event_bus, mode="backtest")
        published_count_when_after_bar_ran = []

        loop.run(
            [make_bar()],
            after_bar=lambda bar: published_count_when_after_bar_ran.append(
                len(fake_event_bus.published)
            ),
        )

        assert published_count_when_after_bar_ran == [1]

    def test_hooks_receive_the_actual_bar_being_processed(self, fake_event_bus, make_bar) -> None:
        loop = TradingLoop(fake_event_bus, mode="backtest")
        bar = make_bar()
        before_bars = []
        after_bars = []

        loop.run([bar], before_bar=before_bars.append, after_bar=after_bars.append)

        assert before_bars == [bar]
        assert after_bars == [bar]

    def test_hooks_are_called_once_per_bar_for_multiple_bars(
        self, fake_event_bus, make_bar
    ) -> None:
        loop = TradingLoop(fake_event_bus, mode="backtest")
        bars = [make_bar(), make_bar(), make_bar()]
        before_calls = []

        loop.run(bars, before_bar=before_calls.append)

        assert before_calls == bars
