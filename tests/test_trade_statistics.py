from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import pytest
from fastapi.testclient import TestClient

from app.config import Mt5ClientConfig, Settings
from app.main import create_app
from app.mt5_gateway import Mt5ExecutionError, Mt5Gateway
from app.multi_trading_service import MultiTradingService
from app.trade_repository import TRADING_TIMEZONE, TradeRepository


def add_close(repository: TradeRepository, signal_id: str, alert_message: str, clients: list[tuple[str, bool]]) -> None:
    repository.insert_fanout(
        signal_id=signal_id,
        source="tradingview",
        action="close_long",
        symbol="XAUUSD",
        payload={"alertMessage": alert_message},
        clients=[(client_id, "XAUUSD", enabled) for client_id, enabled in clients],
    )
    for client_id, enabled in clients:
        if enabled:
            repository.update_signal_status(f"{signal_id}:{client_id}", "success")


def set_execution_time(repository: TradeRepository, signal_id: str, value: str) -> None:
    with repository._connect() as connection:
        connection.execute("UPDATE trade_signals SET executed_at=? WHERE signal_id=? OR parent_signal_id=?", (value, signal_id, signal_id))


def test_repository_classifies_successful_closes_by_inclusive_shanghai_days_and_survives_clearing(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "statistics.db")
    repository.initialize()
    add_close(repository, "take", "anything-else", [("A", True), ("B", False)])
    add_close(repository, "stop", "__STOPLOSS", [("A", True), ("B", False)])
    set_execution_time(repository, "take", "2026-10-16T16:30:00+00:00")
    set_execution_time(repository, "stop", "2026-10-17T15:59:59+00:00")

    result = repository.trade_statistics("2026-10-17", "2026-10-17")
    assert result["take_profit_count"] == 1
    assert result["stop_loss_count"] == 1
    assert result["total_signal_count"] == 2
    assert result["daily"] == [{"date": "2026-10-17", "take_profit": 1, "stop_loss": 1, "net_profit": None}]
    assert repository.trade_statistics("2026-10-17", "2026-10-17", "B")["total_signal_count"] == 0

    assert repository.clear_completed_signals() == 2
    assert repository.trade_statistics("2026-10-17", "2026-10-17")["total_signal_count"] == 2


def make_settings() -> Settings:
    def client(client_id: str) -> Mt5ClientConfig:
        return Mt5ClientConfig(client_id, Path(f"C:/MT5-{client_id}/terminal64.exe"), "XAUUSD",
                               0.01, 0.1, 123, 20, 20, True)

    return Settings(Path("cookie"), Path("payload"), Path("app/static"), "prefix",
        "https://example.test", 20, Path("statistics.db"), None, None, "XAUUSD", 0.01, 0.1,
        123, 20, 20, True, 180, False, (client("A"), client("B")))


class HistoryWorker:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response or {"currency": "AUD", "deals": []}
        self.error = error
        self.ranges = []

    def get_history(self, start: datetime, end: datetime):
        self.ranges.append((start, end))
        if self.error:
            raise self.error
        return self.response


def test_multi_service_aggregates_same_currency_and_keeps_mixed_currencies_separate(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "multi-statistics.db")
    repository.initialize()
    service = MultiTradingService(repository, make_settings())
    deal = {"ticket": 10, "time": "2026-10-16T16:30:00+00:00", "profit": 4.25, "volume": 0.1}
    a = HistoryWorker({"currency": "AUD", "deals": [deal]})
    b = HistoryWorker({"currency": "AUD", "deals": [
        {"ticket": 11, "time": "2026-10-17T16:30:00+00:00", "profit": 99, "volume": 0.1},
    ]})
    service.workers = {"A": a, "B": b}

    same_currency = asyncio.run(service.trade_statistics("2026-10-17", "2026-10-17"))
    assert same_currency["net_profit"] == 4.25
    assert same_currency["profit_currency"] == "AUD"
    assert same_currency["profit_available"] is True
    assert same_currency["daily"][0]["net_profit"] == 4.25
    assert same_currency["recent_deals"][0]["client_id"] == "A"
    start, end = a.ranges[0]
    assert start.astimezone(ZoneInfo("Asia/Shanghai")).isoformat() == "2026-10-17T00:00:00+08:00"
    assert end.astimezone(ZoneInfo("Asia/Shanghai")).isoformat() == "2026-10-18T00:00:00+08:00"

    b.response = {"currency": "USD", "deals": [{**deal, "ticket": 12, "profit": -1.5}]}
    mixed = asyncio.run(service.trade_statistics("2026-10-17", "2026-10-17"))
    assert mixed["net_profit"] is None
    assert mixed["profit_available"] is False
    assert mixed["profit_status"] == "multiple_currencies"
    assert {row["currency"] for row in mixed["profit_by_currency"]} == {"AUD", "USD"}
    assert mixed["daily"][0]["net_profit"] is None

    b.error = RuntimeError("MT5 offline")
    unavailable = asyncio.run(service.trade_statistics("2026-10-17", "2026-10-17"))
    assert unavailable["history_available"] is False
    assert unavailable["net_profit"] is None
    assert unavailable["recent_deals"] == []


def test_history_deals_filters_owned_close_deals_and_returns_account_currency(monkeypatch) -> None:
    gateway = Mt5Gateway(terminal_path=None, symbol="XAUUSD", volume=0.01, max_volume=0.1,
        magic=123, deviation=20, emergency_sl_distance=20, demo_only=False)
    monkeypatch.setattr(gateway, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(gateway, "_validate_account", lambda _account: None)
    monkeypatch.setattr(mt5, "account_info", lambda: SimpleNamespace(currency="AUD"))
    epoch = 1_792_243_800
    close = SimpleNamespace(symbol="XAUUSD", magic=123, entry=mt5.DEAL_ENTRY_OUT,
        time=epoch, ticket=2, profit=5.0, swap=-0.5, commission=-0.2, fee=-0.1,
        volume=0.01, position_id=7)
    wrong_symbol = SimpleNamespace(**{**close.__dict__, "symbol": "EURUSD", "ticket": 3})
    wrong_magic = SimpleNamespace(**{**close.__dict__, "magic": 999, "ticket": 4})
    opening = SimpleNamespace(**{**close.__dict__, "entry": mt5.DEAL_ENTRY_IN, "ticket": 5})
    monkeypatch.setattr(mt5, "history_deals_get", lambda *_args, **kwargs: [] if kwargs else [close, wrong_symbol, wrong_magic, opening])

    start = datetime(2026, 10, 17, tzinfo=TRADING_TIMEZONE)
    result = gateway.history_deals(start, datetime(2026, 10, 18, tzinfo=TRADING_TIMEZONE))
    assert result["currency"] == "AUD"
    assert len(result["deals"]) == 1
    assert result["deals"][0]["profit"] == pytest.approx(4.2)
    assert result["deals"][0]["ticket"] == 2


def test_history_deals_reports_mt5_query_failure(monkeypatch) -> None:
    gateway = Mt5Gateway(terminal_path=None, symbol="XAUUSD", volume=0.01, max_volume=0.1,
        magic=123, deviation=20, emergency_sl_distance=20, demo_only=False)
    monkeypatch.setattr(gateway, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(gateway, "_validate_account", lambda _account: None)
    monkeypatch.setattr(mt5, "account_info", lambda: SimpleNamespace(currency="AUD"))
    monkeypatch.setattr(mt5, "history_deals_get", lambda *_args: None)
    monkeypatch.setattr(mt5, "last_error", lambda: (1, "history unavailable"))
    with pytest.raises(Mt5ExecutionError, match="成交历史失败"):
        gateway.history_deals(datetime.now(timezone.utc), datetime.now(timezone.utc))


def test_trade_statistics_api_validates_dates_accounts_and_uses_shanghai_defaults(tmp_path: Path) -> None:
    class StubTradingService:
        workers = {"A": object()}

        def __init__(self, repository: TradeRepository) -> None:
            self.repository = repository

        async def trade_statistics(self, start_date: str, end_date: str, account: str | None = None):
            return self.repository.trade_statistics(start_date, end_date, account)

    repository = TradeRepository(tmp_path / "api.db")
    repository.initialize()
    app = create_app()
    app.state.trading_service = StubTradingService(repository)
    client = TestClient(app)
    response = client.get("/api/trade-statistics")
    assert response.status_code == 200
    data = response.json()
    assert data["end_date"] == datetime.now(TRADING_TIMEZONE).date().isoformat()
    assert len(data["daily"]) == 7
    assert client.get("/api/trade-statistics?start_date=2026-10-17&end_date=2026-10-16").status_code == 400
    tomorrow = (datetime.now(TRADING_TIMEZONE).date() + timedelta(days=1)).isoformat()
    assert client.get(f"/api/trade-statistics?start_date={tomorrow}&end_date={tomorrow}").status_code == 400
    assert client.get("/api/trade-statistics?start_date=2025-10-16&end_date=2026-10-17").status_code == 400
    assert client.get("/api/trade-statistics?account=B").status_code == 404
    assert client.get("/api/trade-statistics?start_date=bad").status_code == 422


def test_partial_close_allocates_opening_costs_from_before_query(monkeypatch) -> None:
    gateway = Mt5Gateway(terminal_path=None, symbol="XAUUSD", volume=0.01, max_volume=0.1,
        magic=123, deviation=20, emergency_sl_distance=20, demo_only=False)
    monkeypatch.setattr(gateway, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(gateway, "_validate_account", lambda _account: None)
    monkeypatch.setattr(mt5, "account_info", lambda: SimpleNamespace(currency="USD"))
    opening = SimpleNamespace(entry=mt5.DEAL_ENTRY_IN, volume=.1, commission=-2, fee=-.2)
    close = SimpleNamespace(symbol="XAUUSD", magic=123, entry=mt5.DEAL_ENTRY_OUT,
        time=1792243800, ticket=2, order=9, type=mt5.DEAL_TYPE_SELL, reason=mt5.DEAL_REASON_SL,
        profit=5, swap=-.3, commission=-.4, fee=-.1, volume=.04, position_id=7)
    monkeypatch.setattr(mt5, "history_deals_get", lambda *args, **kwargs: [opening, close] if kwargs else [close])
    result = gateway.history_deals(datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert result["deals"][0]["profit"] == pytest.approx(3.32)
    assert result["deals"][0]["side"] == "平多"
    assert result["deals"][0]["exit_reason"] == "止损"


def test_signal_counts_deduplicate_clients_and_use_each_accounts_execution_date(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "boundaries.db")
    repository.initialize()
    add_close(repository, "shared", "__STOPLOSS", [("A", True), ("B", True)])
    set_execution_time(repository, "shared", "2026-10-17T15:59:59+00:00")
    with repository._connect() as connection:
        connection.execute("UPDATE trade_signals SET executed_at=? WHERE signal_id IN (?, ?)",
                           ("2026-10-17T16:00:00+00:00", "shared", "shared:B"))
    all_days = repository.trade_statistics("2026-10-17", "2026-10-18")
    assert all_days["total_signal_count"] == 1
    assert all_days["daily"][0]["stop_loss"] == 1
    assert repository.trade_statistics("2026-10-17", "2026-10-17", "B")["total_signal_count"] == 0
    assert repository.trade_statistics("2026-10-18", "2026-10-18", "B")["total_signal_count"] == 1


def test_history_matches_local_stop_signal_by_order_and_deduplicates_tickets(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "orders.db")
    repository.initialize()
    add_close(repository, "stop", "__STOPLOSS", [("A", True)])
    repository.add_order(signal_id="stop:A", action="close", ticket=99, symbol="XAUUSD", volume=.01,
                         price=2600, retcode=10009, message="done", client_id="A")
    service = MultiTradingService(repository, make_settings())
    deal = {"ticket":2, "order":99, "time":"2026-10-17T12:00:00+00:00", "profit":-3}
    service.workers = {"A":HistoryWorker({"currency":"USD", "deals":[deal, deal]})}
    data = asyncio.run(service.trade_statistics("2026-10-17", "2026-10-17"))
    assert data["closed_deal_count"] == 1
    assert data["net_profit"] == -3
    assert data["recent_deals"][0]["exit_reason"] == "止损"
