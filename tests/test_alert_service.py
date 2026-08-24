from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest

from app.alert_service import AlertService
from app.alert_template import AlertTemplateBuilder
from app.errors import AlertNotFoundError


ROOT = Path(__file__).resolve().parent.parent
PREFIX = "MT5_AU_QQ::GOLD_PRICE::"


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


def build_service(client: FakeTradingViewClient) -> AlertService:
    return AlertService(
        client,
        AlertTemplateBuilder(ROOT / "payload.json"),
        name_prefix=PREFIX,
        webhook_url="http://127.0.0.1:8000/api/webhooks/tradingview",
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
