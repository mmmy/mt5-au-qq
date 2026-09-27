from __future__ import annotations

import asyncio
import json
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


def test_saved_signals_survive_service_restart_and_apply_to_new_alerts(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "trading.db")
    repository.initialize()
    client = FakeTradingViewClient(create_id=55)
    service = build_service(client, repository)
    settings = service.get_signal_settings()
    assert settings == service.template_builder.signal_settings()
    settings.fractal.minute_5 = False
    settings.fractal.minute_2 = False
    settings.pinbar.minute_2 = True
    settings.price_delta = 2.75
    settings.entry_delta = 0
    settings.stop_delta = 4.125
    service.save_signal_settings(settings)
    restarted = build_service(client, TradeRepository(tmp_path / "trading.db"))
    assert restarted.get_signal_settings() == settings
    asyncio.run(restarted.create_alert("4600", UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")))
    inputs = client.created_payloads[0]["payload"]["conditions"][0]["series"][0]["inputs"]
    assert inputs["in_67"] is False
    assert inputs["in_68"] is False
    assert inputs["in_71"] is True
    assert (inputs["in_64"], inputs["in_65"], inputs["in_66"]) == (2.75, 0, 4.125)
    override = settings.model_copy(deep=True)
    override.fractal.minute_5 = True
    asyncio.run(restarted.create_alert(
        "4600", UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), signal_settings=override,
    ))
    assert client.created_payloads[1]["payload"]["conditions"][0]["series"][0]["inputs"]["in_67"] is True
    assert restarted.get_signal_settings() == settings


def test_old_saved_signal_settings_fill_distances_from_template(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "trading.db")
    repository.initialize()
    service = build_service(FakeTradingViewClient(), repository)
    defaults = service.template_builder.signal_settings()
    old = defaults.model_dump(exclude={"price_delta", "entry_delta", "stop_delta"})
    old["fractal"]["minute_5"] = False
    repository.set_runtime_setting("alert_signal_settings", json.dumps(old))
    actual = service.get_signal_settings()
    assert actual.fractal.minute_5 is False
    assert (actual.price_delta, actual.entry_delta, actual.stop_delta) == (
        defaults.price_delta, defaults.entry_delta, defaults.stop_delta,
    )


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
    assert result[0].signal_settings is None


def test_alert_snapshot_survives_defaults_change_restart_and_duplicate_request(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "snapshot.db")
    repository.initialize()
    client = FakeTradingViewClient(create_id=55)
    service = build_service(client, repository)
    requested = service.get_signal_settings().model_copy(deep=True)
    requested.fractal.minute_5 = False
    requested.pinbar.minute_2 = True
    requested.price_delta = 2.75
    requested.entry_delta = 0
    requested.stop_delta = 4.125
    request_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    created = asyncio.run(service.create_alert(
        "4600 4620.5", request_id, side="看空", valid_hours=Decimal("1.01"),
        resolution="2", signal_settings=requested,
    ))
    assert created.alert.signal_settings == requested
    assert created.alert.valid_hours == "1.01"
    assert created.alert.valid_bars == 31
    client.alerts = [{"alert_id": 55, "name": created.alert.name, "active": True,
                      "symbol": "FX:XAUUSD", "resolution": "2"}]
    changed = requested.model_copy(deep=True)
    changed.fractal.minute_5 = True
    changed.price_delta = 9.5
    service.save_signal_settings(changed)
    restarted = build_service(client, TradeRepository(tmp_path / "snapshot.db"))
    listed = asyncio.run(restarted.list_alerts())[0]
    assert listed.signal_settings == requested
    assert listed.valid_hours == "1.01"
    assert listed.side == "看空" and listed.prices == ["4600", "4620.5"]
    duplicate = asyncio.run(restarted.create_alert(
        "4700", request_id, side="看多", valid_hours=Decimal("24"), signal_settings=changed,
    ))
    assert duplicate.created is False
    assert duplicate.alert.signal_settings == requested
    assert duplicate.alert.valid_hours == "1.01"
    assert len(client.created_payloads) == 1


def test_snapshot_uses_completed_inputs_sent_to_tradingview(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "completed.db")
    repository.initialize()
    service = build_service(FakeTradingViewClient(), repository)
    defaults = service.get_signal_settings()
    incomplete = defaults.model_copy(update={"price_delta": None, "entry_delta": None, "stop_delta": None})
    created = asyncio.run(service.create_alert(
        "4600", UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), signal_settings=incomplete,
    ))
    assert created.alert.signal_settings == defaults
    assert repository.get_alert_configs([999])[999]["signal_settings"] == defaults.model_dump(mode="json")
