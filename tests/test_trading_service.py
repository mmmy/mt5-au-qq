from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.errors import ValidationError
from app.models import TradeAction, TradingViewWebhook
from app.trade_repository import TradeRepository
from app.trading_service import TradingService, derive_trade_action, webhook_signal_id


class FakeGateway:
    def status(self):  # pragma: no cover - not needed for ingestion tests
        raise AssertionError("status should not be called")

    def execute(self, _action):  # pragma: no cover - disabled trading must never execute
        raise AssertionError("execute should not be called")

    def shutdown(self) -> None:
        return None


def make_webhook(**overrides: str) -> TradingViewWebhook:
    data = {
        "name": "AU-BOT",
        "side": "buy",
        "exchange": "FX",
        "period": "2",
        "marketPosition": "long",
        "prevMarketPosition": "flat",
        "symbol": "XAUUSD",
        "price": "4600",
        "timestamp": str(int(time.time() * 1000)),
        "size": "1",
        "positionSize": "1",
        "id": "long",
        "alertMessage": "test",
        "comment": "test",
        "qtyType": "fixed",
        "signalToken": "not-stored",
    }
    data.update(overrides)
    return TradingViewWebhook.model_validate(data)


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ("flat", "long", TradeAction.OPEN_LONG),
        ("flat", "short", TradeAction.OPEN_SHORT),
        ("long", "flat", TradeAction.CLOSE_LONG),
        ("short", "flat", TradeAction.CLOSE_SHORT),
        ("short", "long", TradeAction.REVERSE_TO_LONG),
        ("long", "short", TradeAction.REVERSE_TO_SHORT),
    ],
)
def test_derive_trade_action(previous: str, current: str, expected: TradeAction) -> None:
    assert derive_trade_action(previous, current) == expected


def test_derive_trade_action_rejects_unchanged_position() -> None:
    with pytest.raises(ValidationError, match="不支持"):
        derive_trade_action("long", "long")


def test_disabled_webhook_is_saved_once_and_never_queued(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "trading.db")
    repository.initialize()
    service = TradingService(
        repository,
        FakeGateway(),  # type: ignore[arg-type]
        webhook_url="http://127.0.0.1:8000/api/webhooks/tradingview",
        symbol="XAUUSD",
        volume=0.01,
        max_volume=0.1,
        emergency_sl_distance=20,
        demo_only=True,
        signal_max_age_seconds=180,
        enabled_at_start=False,
    )
    payload = make_webhook()

    first = service.ingest_webhook(payload)
    second = service.ingest_webhook(payload)
    row = repository.get_signal(webhook_signal_id(payload))

    assert first.accepted is False
    assert first.status == "blocked"
    assert second.duplicate is True
    assert row is not None
    assert row["action"] == TradeAction.OPEN_LONG.value
    assert "not-stored" not in row["payload_json"]
    assert len(repository.list_signals()) == 1


def test_webhook_rejects_expired_signal(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "trading.db")
    repository.initialize()
    service = TradingService(
        repository,
        FakeGateway(),  # type: ignore[arg-type]
        webhook_url="http://127.0.0.1:8000/api/webhooks/tradingview",
        symbol="XAUUSD",
        volume=0.01,
        max_volume=0.1,
        emergency_sl_distance=20,
        demo_only=True,
        signal_max_age_seconds=30,
        enabled_at_start=False,
    )

    with pytest.raises(ValidationError, match="过期"):
        service.ingest_webhook(make_webhook(timestamp="1000"))


def test_clear_completed_signals_hides_records_but_preserves_deduplication(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "trading.db")
    repository.initialize()
    repository.insert_signal(
        signal_id="completed",
        source="tradingview",
        action="open_long",
        status="blocked",
        symbol="XAUUSD",
        payload={"test": True},
    )
    repository.insert_signal(
        signal_id="active",
        source="tradingview",
        action="open_long",
        status="queued",
        symbol="XAUUSD",
        payload={"test": True},
    )

    assert repository.clear_completed_signals() == 1
    assert [item.signal_id for item in repository.list_signals()] == ["active"]
    assert repository.get_signal("completed") is not None
    assert repository.insert_signal(
        signal_id="completed",
        source="tradingview",
        action="open_long",
        status="queued",
        symbol="XAUUSD",
        payload={"test": True},
    ) is False
