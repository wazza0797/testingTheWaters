# Milestone 7 — Notifications

**Status:** Complete

**Depends on:** Milestone 6 (paper loop publishes fills / heartbeats)

**Unblocks:** Milestone 8 (live alerts), Milestone 9 (ops visibility)

## Goals

Alert on trades, risk rejects, errors, and heartbeats — **decoupled** via
event subscriptions so execution/risk never import notifiers.

## Design decisions

1. **Channels:** `ConsoleNotifier` always; optional `DiscordNotifier`
   (`DISCORD_WEBHOOK_URL`) and/or `TelegramNotifier` (both
   `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`). Missing remotes → console only.
2. **Fan-out:** `CompositeNotifier` inside `NotificationHandler`
3. **Isolation:** handler catches its own exceptions; composite isolates
   per-channel failures so one remote never blocks console (or vice versa).
   `NotificationHandler` dispatches via a thread pool so Discord/Telegram HTTP
   never stalls the event-bus / portfolio path. Backtest unsubscribes fill/
   reject notifications (same pattern as analytics) to avoid remote spam.
4. **Out of scope:** daily digest scheduling, email, live-order audit

## Components

| Component | Path | Role |
|-----------|------|------|
| Port | `domain/ports/notification.py` | `INotifier` (already existed) |
| Console | `notifications/console.py` | stdout channel |
| Discord | `notifications/discord.py` | Incoming webhook via httpx |
| Telegram | `notifications/telegram.py` | Bot API `sendMessage` via httpx |
| Composite | `notifications/composite.py` | Fan-out with per-channel isolation |
| Handler | `notifications/handler.py` | Subscribe → format → notify |
| Wiring | `container.py` | Build composite; subscribe on bus |

## Events handled

- `FillReceived` — fill landed
- `RiskRejected` — trading-policy reject (no Order created)
- `OrderRejected` — exchange-rule reject (Order failed min notional / step / etc.)
- `ErrorOccurred` — unrecoverable error event (when published)
- `Heartbeat` — paper idle + observability loop

**Follow-up:** mute or throttle `Heartbeat` on Telegram (keep console) — too
noisy at every poll interval. See TODO in `notifications/handler.py`.

## Acceptance criteria

- Paper fill triggers console message; Discord/Telegram when configured
- Missing remote creds → graceful degradation (console only)
- Unit tests mock Discord/Telegram HTTP (no real API calls)
- `uv run pytest -m "not network"` / mypy / ruff green
