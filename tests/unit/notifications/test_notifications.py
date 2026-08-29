from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import httpx
import pytest

from trading_platform.config.settings import Settings
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import RiskRejected
from trading_platform.domain.events.system import ErrorOccurred, Heartbeat
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus
from trading_platform.notifications.composite import CompositeNotifier
from trading_platform.notifications.console import ConsoleNotifier
from trading_platform.notifications.discord import DiscordNotifier
from trading_platform.notifications.factory import build_notifier
from trading_platform.notifications.handler import NotificationHandler, format_event
from trading_platform.notifications.telegram import TelegramNotifier


def _sample_order() -> Order:
    return Order(
        order_id="ord-1",
        correlation_id="corr-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        price=None,
        strategy_name="test",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _sample_fill() -> Fill:
    return Fill(
        order_id="ord-1",
        correlation_id="corr-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        filled_qty=Decimal("0.01"),
        remaining_qty=Decimal("0"),
        fill_price=Decimal("100"),
        fee=Decimal("0.01"),
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _sample_signal() -> Signal:
    return Signal(
        symbol="BTC/USDT",
        signal_type=SignalType.BUY,
        strategy_name="test",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        strength=0.8,
    )


class TestFormatEvent:
    def test_formats_fill(self) -> None:
        event = FillReceived(fill=_sample_fill(), order=_sample_order())
        message, level = format_event(event)  # type: ignore[misc]
        assert level == "info"
        assert "FILL buy BTC/USDT" in message
        assert "qty=0.01" in message

    def test_formats_risk_rejected(self) -> None:
        event = RiskRejected(signal=_sample_signal(), reason="too big")
        message, level = format_event(event)  # type: ignore[misc]
        assert level == "warning"
        assert "RISK REJECTED buy BTC/USDT" in message
        assert "too big" in message

    def test_formats_order_rejected(self) -> None:
        event = OrderRejected(order=_sample_order(), reason="min_notional")
        message, level = format_event(event)  # type: ignore[misc]
        assert level == "warning"
        assert "ORDER REJECTED buy BTC/USDT" in message
        assert "min_notional" in message

    def test_formats_error_and_heartbeat(self) -> None:
        err = ErrorOccurred(source="x", error_type="Y", message="boom")
        hb = Heartbeat(mode="paper", uptime_seconds=12.5)
        assert format_event(err) == ("ERROR source=x type=Y: boom", "error")
        message, level = format_event(hb)  # type: ignore[misc]
        assert level == "info"
        assert "HEARTBEAT mode=paper" in message

    def test_ignores_unsupported(self, make_bar) -> None:
        assert format_event(BarClosed(bar=make_bar(), mode="paper")) is None


class TestConsoleAndComposite:
    def test_console_writes_level_prefix(self) -> None:
        buf = StringIO()
        ConsoleNotifier(buf).notify("hello", level="warning")
        assert buf.getvalue() == "[WARNING] hello\n"

    def test_composite_isolates_failing_channel(self) -> None:
        ok = ConsoleNotifier(StringIO())
        calls: list[str] = []

        class Boom:
            def notify(self, message: str, level: str = "info") -> None:
                raise RuntimeError("nope")

        class Tracking:
            def notify(self, message: str, level: str = "info") -> None:
                calls.append(message)

        CompositeNotifier([ok, Boom(), Tracking()]).notify("ping")
        assert calls == ["ping"]


class TestTelegramNotifier:
    def test_posts_send_message(self) -> None:
        captured: dict[str, object] = {}

        def fake_post(url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return httpx.Response(200, json={"ok": True, "result": {}})

        notifier = TelegramNotifier("tok", "123", http_post=fake_post)
        notifier.notify("hi", level="info")
        assert captured["url"] == "https://api.telegram.org/bottok/sendMessage"
        assert captured["json"] == {"chat_id": "123", "text": "[INFO] hi"}

    def test_raises_on_api_not_ok(self) -> None:
        def fake_post(url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(200, json={"ok": False, "description": "bad chat"})

        notifier = TelegramNotifier("tok", "123", http_post=fake_post)
        with pytest.raises(RuntimeError, match="bad chat"):
            notifier.notify("hi")


class TestDiscordNotifier:
    def test_posts_webhook_content(self) -> None:
        captured: dict[str, object] = {}

        def fake_post(url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return httpx.Response(204)

        notifier = DiscordNotifier(
            "https://discord.com/api/webhooks/1/abc",
            http_post=fake_post,
        )
        notifier.notify("hi", level="warning")
        assert captured["url"] == "https://discord.com/api/webhooks/1/abc"
        assert captured["json"] == {"content": "[WARNING] hi"}

    def test_raises_on_http_error(self) -> None:
        def fake_post(url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(401, text="unauthorized")

        notifier = DiscordNotifier(
            "https://discord.com/api/webhooks/1/abc",
            http_post=fake_post,
        )
        with pytest.raises(RuntimeError, match="Discord HTTP 401"):
            notifier.notify("hi")

    def test_truncates_overlong_content(self) -> None:
        captured: dict[str, object] = {}

        def fake_post(url: str, **kwargs: object) -> httpx.Response:
            captured["json"] = kwargs["json"]
            return httpx.Response(204)

        notifier = DiscordNotifier(
            "https://discord.com/api/webhooks/1/abc",
            http_post=fake_post,
        )
        notifier.notify("x" * 2500)
        content = captured["json"]["content"]  # type: ignore[index]
        assert isinstance(content, str)
        assert len(content) == 2000
        assert content.endswith("…")


class TestBuildNotifier:
    def test_console_only_without_remote_creds(self) -> None:
        settings = Settings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN=None,
            TELEGRAM_CHAT_ID=None,
            DISCORD_WEBHOOK_URL=None,
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, CompositeNotifier)
        assert len(notifier.notifiers) == 1
        assert isinstance(notifier.notifiers[0], ConsoleNotifier)

    def test_includes_telegram_when_configured(self) -> None:
        settings = Settings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN="tok",
            TELEGRAM_CHAT_ID="42",
            DISCORD_WEBHOOK_URL=None,
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, CompositeNotifier)
        assert len(notifier.notifiers) == 2
        assert isinstance(notifier.notifiers[1], TelegramNotifier)

    def test_includes_discord_when_configured(self) -> None:
        settings = Settings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN=None,
            TELEGRAM_CHAT_ID=None,
            DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/1/abc",
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, CompositeNotifier)
        assert len(notifier.notifiers) == 2
        assert isinstance(notifier.notifiers[1], DiscordNotifier)

    def test_includes_both_remotes_when_configured(self) -> None:
        settings = Settings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN="tok",
            TELEGRAM_CHAT_ID="42",
            DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/1/abc",
        )
        notifier = build_notifier(settings)
        assert isinstance(notifier, CompositeNotifier)
        assert len(notifier.notifiers) == 3
        assert isinstance(notifier.notifiers[1], TelegramNotifier)
        assert isinstance(notifier.notifiers[2], DiscordNotifier)


class TestNotificationHandler:
    def test_reacts_to_published_events_not_direct_peer_calls(self) -> None:
        buf = StringIO()
        handler = NotificationHandler(ConsoleNotifier(buf))
        bus = InMemoryEventBus()
        bus.subscribe(FillReceived, handler)
        bus.subscribe(RiskRejected, handler)
        bus.subscribe(OrderRejected, handler)

        bus.publish(FillReceived(fill=_sample_fill(), order=_sample_order()))
        bus.publish(RiskRejected(signal=_sample_signal(), reason="cap"))
        bus.publish(OrderRejected(order=_sample_order(), reason="min_qty"))

        text = buf.getvalue()
        assert "FILL buy BTC/USDT" in text
        assert "RISK REJECTED buy BTC/USDT" in text
        assert "ORDER REJECTED buy BTC/USDT" in text
        assert "cap" in text
        assert "min_qty" in text

    def test_swallows_notifier_exceptions(self) -> None:
        class Boom:
            def notify(self, message: str, level: str = "info") -> None:
                raise RuntimeError("down")

        handler = NotificationHandler(Boom())
        handler.handle(Heartbeat(mode="paper", uptime_seconds=1.0))

    def test_async_dispatch_returns_before_notify_finishes(self) -> None:
        import threading
        from concurrent.futures import ThreadPoolExecutor

        started = threading.Event()
        release = threading.Event()
        done: list[str] = []

        class Slow:
            def notify(self, message: str, level: str = "info") -> None:
                started.set()
                assert release.wait(timeout=2.0)
                done.append(message)

        with ThreadPoolExecutor(max_workers=1) as executor:
            handler = NotificationHandler(Slow(), executor=executor)
            handler.handle(Heartbeat(mode="paper", uptime_seconds=1.0))
            assert started.wait(timeout=1.0)
            assert done == []
            release.set()
        assert done == ["HEARTBEAT mode=paper uptime=1.0s"]
