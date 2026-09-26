from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Mt5ClientConfig, Settings
from app.models import Mt5Status, TradeAction, TradingViewWebhook
from app.mt5_gateway import ExecutedOrder, Mt5ExecutionError, Mt5Gateway
from app.mt5_process import ProcessMt5Gateway
from app.multi_trading_service import MultiTradingService
from app.trade_repository import TradeRepository


class SimulatedGateway:
    """Picklable terminal simulation; never imports or calls a live connection."""
    def __init__(self, **options):
        self.symbol = options["symbol"]
        self.volume = options["volume"]
        self.count = 0

    def status(self):
        return Mt5Status(initialized=True, connected=True, terminal_trade_allowed=True,
            account_trade_allowed=True, account_trade_expert=True, demo_account=True,
            symbol=self.symbol, symbol_available=True, server=str(os.getpid()),
            owned_long_positions=self.count)

    def execute(self, action):
        if self.symbol == "FAIL":
            raise RuntimeError("模拟下单失败")
        if self.symbol == "SLOW":
            time.sleep(0.3)
        self.count += 1
        return [ExecutedOrder(action.value, self.count, self.symbol, self.volume, 4600, 10009, "simulated")]

    def shutdown(self):
        pass


def config(key, symbol="XAUUSD"):
    return Mt5ClientConfig(key, Path(f"C:/MT5-{key}/terminal64.exe"), symbol, 0.01, 0.1,
                           100, 20, 20, True)


def settings():
    return Settings(Path("unused-cookie"), Path("unused-payload"), Path("app/static"),
        "prefix", "https://cn.tradingview.com", 20, Path("unused.db"), None, None,
        "XAUUSD", 0.01, 0.1, 100, 20, 20, True, 180, False,
        (config("A"), config("B", "XAUUSDm")))


def webhook():
    return TradingViewWebhook(name="AU-BOT", side="buy", marketPosition="long",
        prevMarketPosition="flat", symbol="XAUUSD", timestamp=str(time.time()),
        id="long", signalToken="secret")


def wait_completed(repo, signal_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = repo.get_signal(signal_id)
        if row["status"] not in {"queued", "running"}:
            return row
        time.sleep(0.01)
    raise AssertionError("Tasks did not finish")


def test_windows_spawn_isolates_connections():
    a = ProcessMt5Gateway(config("A"), gateway_factory=SimulatedGateway)
    b = ProcessMt5Gateway(config("B"), gateway_factory=SimulatedGateway)
    try:
        assert a.status().server != b.status().server
        a.execute(TradeAction.OPEN_LONG)
        assert a.status().owned_long_positions == 1
        assert b.status().owned_long_positions == 0
    finally:
        a.shutdown()
        b.shutdown()
    assert a._process is None and b._process is None


def test_timeout_does_not_replay_order():
    gateway = ProcessMt5Gateway(config("A", "SLOW"), gateway_factory=SimulatedGateway)
    try:
        gateway.status()
        gateway.timeout = 0.05
        with pytest.raises(Mt5ExecutionError, match="不会自动重试"):
            gateway.execute(TradeAction.OPEN_LONG)
        assert gateway._process is None
    finally:
        gateway.shutdown()


def test_fanout_deduplication_and_independent_failure(tmp_path):
    repo = TradeRepository(tmp_path / "trading.db")
    configs = (config("A"), config("B", "FAIL"))
    service = MultiTradingService(repo, replace(settings(), mt5_clients=configs),
        gateway_factory=lambda c: SimulatedGateway(symbol=c.symbol, volume=c.volume))
    service.start()
    try:
        asyncio.run(service.enable())
        payload = webhook()
        first = service.ingest_webhook(payload)
        second = service.ingest_webhook(payload)
        assert second.duplicate
        assert wait_completed(repo, first.signal_id)["status"] == "partial"
        rows = repo.list_signals()
        assert len(rows) == 1
        assert {x["client_id"]: x["status"] for x in rows[0].executions} == {"A": "success", "B": "failed"}
        assert "secret" not in repo.get_signal(first.signal_id)["payload_json"]
        orders = repo.list_orders()
        assert len(orders) == 1 and orders[0].client_id == "A"
        assert repo.clear_completed_signals() == 1
        assert service.ingest_webhook(payload).duplicate
    finally:
        service.stop()


def test_client_switch_manual_target_and_persistence(tmp_path):
    repo = TradeRepository(tmp_path / "trading.db")
    factory = lambda c: SimulatedGateway(symbol=c.symbol, volume=c.volume)
    service = MultiTradingService(repo, settings(), gateway_factory=factory)
    service.start()
    try:
        asyncio.run(service.enable())
        asyncio.run(service.toggle_client("B", False))
        result = service.submit_manual_action(TradeAction.CLOSE_LONG, "A")
        assert wait_completed(repo, result.signal_id)["status"] == "success"
        assert len(repo.list_signals()[0].executions) == 1
        result = service.ingest_webhook(webhook())
        assert wait_completed(repo, result.signal_id)["status"] == "partial"
        status = asyncio.run(service.runtime_status())
        assert [c.enabled for c in status.clients] == [True, False]
        assert status.clients[1].mt5.symbol == "XAUUSDm"
        with pytest.raises(Exception, match="未知"):
            service.submit_manual_action(TradeAction.CLOSE_LONG, "C")
    finally:
        service.stop()
    restored = MultiTradingService(repo, settings(), gateway_factory=factory)
    restored.start()
    try:
        assert restored.is_enabled() and not restored.client_enabled["B"]
    finally:
        restored.stop()


def test_recovery_does_not_retry_running_or_removed_clients(tmp_path):
    repo = TradeRepository(tmp_path / "trading.db")
    repo.initialize()
    repo.insert_fanout(signal_id="pending", source="manual", action="open_long", symbol="XAUUSD",
        payload={}, clients=[("A", "XAUUSD", True), ("B", "XAUUSDm", True)])
    repo.update_signal_status("pending:A", "running")
    assert repo.recover_client_tasks(["A"]) == []
    assert repo.get_signal("pending:A")["status"] == "failed"
    assert repo.get_signal("pending:B")["status"] == "blocked"


def test_configuration_legacy_and_shared_parameters(monkeypatch):
    monkeypatch.setattr("app.config.load_dotenv", lambda *args, **kwargs: None)
    for name in list(os.environ):
        if name.startswith("MT5_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("MT5_TERMINAL_PATH", "C:/legacy/terminal64.exe")
    assert Settings.from_env().clients()[0].terminal_path == Path("C:/legacy/terminal64.exe")
    monkeypatch.setenv("MT5_A_TERMINAL_PATH", "C:/A/terminal64.exe")
    monkeypatch.setenv("MT5_B_TERMINAL_PATH", "C:/B/terminal64.exe")
    monkeypatch.setenv("MT5_VOLUME", "0.02")
    monkeypatch.setenv("MT5_B_VOLUME", "0.03")
    monkeypatch.setenv("MT5_SYMBOL", "GOLD")
    monkeypatch.setenv("MT5_B_SYMBOL", "ignored-symbol")
    monkeypatch.setenv("MT5_MAX_VOLUME", "0.2")
    monkeypatch.setenv("MT5_MAGIC", "789")
    monkeypatch.setenv("MT5_DEVIATION", "30")
    monkeypatch.setenv("MT5_EMERGENCY_SL_DISTANCE", "15")
    monkeypatch.setenv("MT5_DEMO_ONLY", "true")
    clients = Settings.from_env().clients()
    assert [c.volume for c in clients] == [0.02, 0.02]
    assert [c.symbol for c in clients] == ["GOLD", "GOLD"]
    for client in clients:
        assert client.max_volume == 0.2
        assert client.magic == 789
        assert client.deviation == 30
        assert client.emergency_sl_distance == 15
        assert client.demo_only is True
        assert client.expected_login is None and client.expected_server is None
    monkeypatch.setenv("MT5_B_TERMINAL_PATH", "C:/A/terminal64.exe")
    with pytest.raises(ValueError, match="不同"):
        Settings.from_env()
    monkeypatch.delenv("MT5_A_TERMINAL_PATH")
    with pytest.raises(ValueError, match="同时"):
        Settings.from_env()


def test_account_identity_is_checked_before_order(monkeypatch):
    from types import SimpleNamespace
    gateway = Mt5Gateway(terminal_path=None, symbol="XAUUSD", volume=0.01, max_volume=0.1,
        magic=100, deviation=20, emergency_sl_distance=20, demo_only=True,
        expected_login=123, expected_server="Demo")
    monkeypatch.setattr("app.mt5_gateway.mt5.account_info", lambda: SimpleNamespace(login=999, server="Demo"))
    monkeypatch.setattr("app.mt5_gateway.mt5.order_send", lambda *args: pytest.fail("must not send order"))
    with pytest.raises(Mt5ExecutionError, match="账号"):
        gateway._send_deal(order_type=0, volume=0.01, position_ticket=None, action="open")
    with pytest.raises(Mt5ExecutionError, match="服务器"):
        gateway._validate_account(SimpleNamespace(login=123, server="Other"))


def test_queued_tasks_recover_and_expire_independently(tmp_path):
    repo = TradeRepository(tmp_path / "trading.db")
    repo.initialize()
    repo.set_runtime_setting("trading_enabled", "1")
    for signal_id in ("fresh", "expired"):
        repo.insert_fanout(signal_id=signal_id, source="tradingview", action="open_long", symbol="XAUUSD",
            payload={"timestamp": str(time.time() if signal_id == "fresh" else time.time() - 3600)},
            clients=[("A", "XAUUSD", True), ("B", "XAUUSDm", True)])
    service = MultiTradingService(repo, settings(),
        gateway_factory=lambda c: SimulatedGateway(symbol=c.symbol, volume=c.volume))
    service.start()
    try:
        assert wait_completed(repo, "fresh")["status"] == "success"
        assert wait_completed(repo, "expired")["status"] == "expired"
        assert len(repo.list_orders()) == 2
    finally:
        service.stop()


def test_api_with_two_spawned_simulated_terminals(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    monkeypatch.setattr(main, "MultiTradingService", lambda repository, config:
        MultiTradingService(repository, config, gateway_factory=lambda c:
            ProcessMt5Gateway(c, gateway_factory=SimulatedGateway)))
    configured = replace(settings(), database_file=tmp_path / "api.db")
    with TestClient(main.create_app(configured)) as client:
        status = client.get("/api/trading/status").json()
        assert len(status["clients"]) == 2 and not status["enabled"]
        assert status["clients"][0]["mt5"]["server"] != status["clients"][1]["mt5"]["server"]
        assert client.post("/api/trading/enable").status_code == 200
        result = client.post("/api/mt5/actions/open_long?client_id=B")
        assert result.status_code == 202
        wait_completed(client.app.state.trading_service.repository, result.json()["signal_id"])
        orders = client.get("/api/trade-orders").json()
        assert len(orders) == 1 and orders[0]["client_id"] == "B" and orders[0]["symbol"] == "XAUUSDm"
        assert client.post("/api/trading/clients/A/disable").json()["enabled"] is False
        assert client.post("/api/mt5/actions/open_long?client_id=A").status_code == 409
        assert client.post("/api/trading/clients/C/disable").status_code == 400
        assert client.get("/").status_code == 200
