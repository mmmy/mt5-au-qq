from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.alert_service import AlertService
from app.alert_template import AlertTemplateBuilder
from app.errors import AlertNotFoundError
from app.trade_repository import TradeRepository


ROOT = Path(__file__).resolve().parent.parent
PREFIX = "MT5_AU::GOLD_PRICE::"


class FakeTradingViewClient:
    def __init__(self, alerts: list[dict] | None = None, create_id: int | None = 999) -> None:
        self.alerts = alerts or []
        self.create_id = create_id
        self.created_payloads: list[dict] = []
        self.deleted_ids: list[list[int]] = []

    async def list_alerts(self) -> list[dict]:
        return self.alerts

    async def create_alert(self, payload: dict) -> int | None:
        self.created_payloads.append(payload)
        return self.create_id

    async def delete_alerts(self, alert_ids: list[int]) -> None:
        self.deleted_ids.append(alert_ids)


def build_service(client: FakeTradingViewClient, repository: TradeRepository | None = None) -> AlertService:
    return AlertService(
        client,
        AlertTemplateBuilder(ROOT / "payload.json"),
        name_prefix=PREFIX,
        webhook_url="http://127.0.0.1:8000/api/webhooks/tradingview",
        repository=repository,
    )  # type: ignore[arg-type]


def test_list_only_returns_project_alerts_and_normalizes_symbol() -> None:
    client = FakeTradingViewClient(
        [
            {
                "alert_id": 12,
                "name": PREFIX + "abc",
                "active": True,
                "symbol": '={"currency-id":"USD","symbol":"FX:XAUUSD"}',
                "resolution": "2",
                "create_time": "2026-08-23T10:00:00Z",
            },
            {"alert_id": 13, "name": "OTHER", "active": True, "symbol": "FX:XAUUSD", "resolution": "2"},
        ]
    )

    result = asyncio.run(build_service(client).list_alerts())

    assert len(result) == 1
    assert result[0].alert_id == 12
    assert result[0].symbol == "FX:XAUUSD"


def test_create_builds_payload_and_returns_normalized_prices() -> None:
    client = FakeTradingViewClient(create_id=55)
    service = build_service(client)

    result = asyncio.run(
        service.create_alert(
            "4600.00 4620.5",
            UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            webhook_url="https://trade.example.com/api/webhooks/tradingview",
        )
    )

    assert result.created is True
    assert result.prices == ["4600", "4620.5"]
    assert result.alert.alert_id == 55
    assert result.alert.name == PREFIX + "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"
    assert len(client.created_payloads) == 1
    assert client.created_payloads[0]["payload"]["web_hook"] == "https://trade.example.com/api/webhooks/tradingview"


def test_create_converts_valid_hours_and_returns_actual_bars() -> None:
    client = FakeTradingViewClient(create_id=56)
    service = build_service(client)

    result = asyncio.run(
        service.create_alert(
            "4600",
            UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            valid_hours=Decimal("1"),
            resolution="240",
        )
    )

    assert result.alert.valid_bars == 1
    assert result.alert.end_time_ms - result.alert.start_time_ms == 4 * 60 * 60 * 1000


def test_create_is_idempotent_for_existing_request_id() -> None:
    name = PREFIX + "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa"
    client = FakeTradingViewClient(
        [{"alert_id": 77, "name": name, "active": True, "symbol": "FX:XAUUSD", "resolution": "2"}]
    )

    result = asyncio.run(
        build_service(client).create_alert("4600", UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
    )

    assert result.created is False
    assert result.alert.alert_id == 77
    assert client.created_payloads == []


def test_delete_only_allows_project_alerts() -> None:
    client = FakeTradingViewClient(
        [{"alert_id": 13, "name": "OTHER", "active": True, "symbol": "FX:XAUUSD", "resolution": "2"}]
    )

    with pytest.raises(AlertNotFoundError):
        asyncio.run(build_service(client).delete_alert(13))

    assert client.deleted_ids == []


def test_delete_project_alert() -> None:
    client = FakeTradingViewClient(
        [{"alert_id": 12, "name": PREFIX + "abc", "active": True, "symbol": "FX:XAUUSD", "resolution": "2"}]
    )

    result = asyncio.run(build_service(client).delete_alert(12))

    assert result.deleted is True
    assert client.deleted_ids == [[12]]


def test_list_enriches_tradingview_alert_with_saved_strategy_settings(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "trading.db")
    repository.initialize()
    repository.upsert_alert_config(
        alert_id=12,
        prices=["4600", "4620.5"],
        side="看多",
        valid_bars=288,
        start_time_ms=1787582700000,
        end_time_ms=1787669100000,
        resolution="5",
    )
    client = FakeTradingViewClient(
        [{"alert_id": 12, "name": PREFIX + "abc", "active": True, "symbol": "FX:XAUUSD", "resolution": "5"}]
    )

    result = asyncio.run(build_service(client, repository).list_alerts())

    assert result[0].prices == ["4600", "4620.5"]
    assert result[0].side == "看多"
    assert result[0].valid_bars == 288
    assert result[0].start_time_ms == 1787582700000
    assert result[0].end_time_ms == 1787669100000
