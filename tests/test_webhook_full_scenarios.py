"""Exercise webhook -> SQLite -> worker -> real gateway code with fake MT5 I/O.

No real terminal connection or TradingView request is made by these tests.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import MetaTrader5 as real_mt5
import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.mt5_gateway as gateway_module
from app.config import Mt5ClientConfig, Settings
from app.mt5_gateway import Mt5Gateway
from app.multi_trading_service import MultiTradingService


class FakeTerminal:
    def __init__(self):
        self.bid, self.ask = 4600.0, 4600.2
        self.requests = []
        self.next_ticket = 1000
        self.positions = [SimpleNamespace(ticket=99, type=0, magic=999, volume=0.5, symbol="XAUUSD")]
        self.reject_orders = False
        self.trade_allowed = True

    def __getattr__(self, name):
        value = getattr(real_mt5, name)
        if callable(value):
            raise AssertionError(f"Unmocked MT5 operation: {name}")
        return value

    def initialize(self, *args, **kwargs): return True
    def shutdown(self): pass
    def terminal_info(self): return SimpleNamespace(connected=True, trade_allowed=self.trade_allowed)
    def account_info(self):
        return SimpleNamespace(login=123456, server="Isolated Demo", trade_mode=real_mt5.ACCOUNT_TRADE_MODE_DEMO,
                               trade_allowed=True, trade_expert=True)
    def symbol_select(self, *args): return True
    def symbol_info(self, symbol):
        return SimpleNamespace(trade_mode=real_mt5.SYMBOL_TRADE_MODE_FULL, volume_min=0.01,
                               volume_max=100, volume_step=0.01, digits=2, filling_mode=3)
    def symbol_info_tick(self, symbol): return SimpleNamespace(bid=self.bid, ask=self.ask)
    def positions_get(self, **kwargs): return tuple(self.positions)
    def order_check(self, request): return SimpleNamespace(retcode=0, comment="OK")
    def order_send(self, request):
        self.requests.append(dict(request))
        if self.reject_orders:
            return SimpleNamespace(retcode=real_mt5.TRADE_RETCODE_MARKET_CLOSED, comment="Market closed")
        if "position" in request:
            position = next(p for p in self.positions if p.ticket == request["position"])
            assert request["volume"] == position.volume
            self.positions.remove(position)
            ticket = position.ticket
        else:
            self.next_ticket += 1
            ticket = self.next_ticket
            self.positions.append(SimpleNamespace(ticket=ticket, type=request["type"], magic=request["magic"],
                volume=request["volume"], symbol=request["symbol"], sl=request.get("sl", 0)))
        return SimpleNamespace(retcode=real_mt5.TRADE_RETCODE_DONE, comment="Simulated fill", order=ticket,
                               deal=ticket, volume=request["volume"], price=request["price"])

    def owned(self): return [p for p in self.positions if p.magic == 100]


@pytest.fixture
def webhook_app(tmp_path, monkeypatch):
    terminal = FakeTerminal()
    monkeypatch.setattr(gateway_module, "mt5", terminal)
    def gateway(config):
        return Mt5Gateway(terminal_path=config.terminal_path, symbol=config.symbol, volume=config.volume,
            max_volume=config.max_volume, magic=config.magic, deviation=config.deviation,
            emergency_sl_distance=config.emergency_sl_distance, demo_only=True)
    monkeypatch.setattr(main, "MultiTradingService", lambda repo, settings:
                        MultiTradingService(repo, settings, gateway_factory=gateway))
    root = Path(__file__).resolve().parent.parent
    config = Mt5ClientConfig("A", Path("C:/Isolated-MT5/terminal64.exe"), "XAUUSD", 0.01, 0.1, 100, 20, 20, True)
    settings = Settings(root / "unused-cookie", root / "payload.json", root / "app/static", "TEST::",
        "https://cn.tradingview.com", 20, tmp_path / "isolated.db", None, None, "XAUUSD", 0.01, 0.1,
        100, 20, 20, True, 180, False, (config,))
    with TestClient(main.create_app(settings)) as client:
        assert client.post("/api/trading/clients/A/enable").status_code == 200
        yield client, terminal
        assert any(p.ticket == 99 and p.magic == 999 for p in terminal.positions)


def tv_payload(previous="flat", current="long", **changes):
    data = {"name": "AU-BOT", "side": "buy" if current == "long" or previous == "short" else "sell",
        "exchange": "FX", "period": "2", "marketPosition": current, "prevMarketPosition": previous,
        "symbol": "XAUUSD", "price": "4612.3400", "timestamp": datetime.now(timezone.utc).isoformat(),
        "size": "1", "positionSize": "0" if current == "flat" else "1", "id": "isolated-" + uuid4().hex,
        "alertMessage": "", "comment": "Isolated webhook test", "qtyType": "fixed", "signalToken": ""}
    data.update(changes)
    return data


def post_and_wait(client, payload, action, status="success"):
    response = client.post("/api/webhooks/tradingview", json=payload)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["action"] == action
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        signals = client.get("/api/trade-signals").json()
        signal = next(s for s in signals if s["signal_id"] == body["signal_id"])
        if signal["status"] not in {"queued", "running"}:
            assert signal["status"] == status, signal
            assert signal["trigger_price"] == payload["price"]
            return signal
        time.sleep(0.01)
    pytest.fail("Webhook worker did not finish")


@pytest.mark.parametrize("direction,position_type,stop_id", [("long", 0, "long-stop"), ("short", 1, "short-stop")])
def test_open_repeat_and_strategy_stop_loss(webhook_app, direction, position_type, stop_id):
    client, terminal = webhook_app
    opening = tv_payload(current=direction)
    post_and_wait(client, opening, "open_" + direction)
    position = terminal.owned()[0]
    assert position.type == position_type and position.volume == 0.01
    assert position.sl == (4580.2 if direction == "long" else 4620.0)
    assert client.post("/api/webhooks/tradingview", json=opening).json()["duplicate"] is True
    assert len(terminal.requests) == 1
    post_and_wait(client, tv_payload(current=direction), "open_" + direction)
    assert len(terminal.requests) == 1
    post_and_wait(client, tv_payload(direction, "flat", id=stop_id + uuid4().hex,
                                   alertMessage="__STOPLOSS", comment="损"), "close_" + direction)
    assert not terminal.owned()
    assert "sl" not in terminal.requests[-1]
    assert terminal.requests[-1]["position"] == position.ticket
    assert len(client.get("/api/trade-orders").json()) == 2


@pytest.mark.parametrize("direction", ["long", "short"])
def test_take_profit_and_repeated_close(webhook_app, direction):
    client, terminal = webhook_app
    post_and_wait(client, tv_payload(current=direction), "open_" + direction)
    post_and_wait(client, tv_payload(direction, "flat", comment="多头止盈" if direction == "long" else "空头止盈"), "close_" + direction)
    count = len(terminal.requests)
    post_and_wait(client, tv_payload(direction, "flat"), "close_" + direction)
    assert len(terminal.requests) == count and not terminal.owned()


def test_both_reversal_directions_close_before_open(webhook_app):
    client, terminal = webhook_app
    post_and_wait(client, tv_payload(), "open_long")
    post_and_wait(client, tv_payload("long", "short"), "reverse_to_short")
    assert [p.type for p in terminal.owned()] == [1]
    assert "position" in terminal.requests[-2] and "position" not in terminal.requests[-1]
    post_and_wait(client, tv_payload("short", "long"), "reverse_to_long")
    assert [p.type for p in terminal.owned()] == [0]
    assert "position" in terminal.requests[-2] and "position" not in terminal.requests[-1]
    post_and_wait(client, tv_payload("long", "flat"), "close_long")
    assert not terminal.owned()


@pytest.mark.parametrize("direction", ["long", "short"])
def test_broker_stop_loss_then_tv_stop_does_not_reopen(webhook_app, direction):
    client, terminal = webhook_app
    post_and_wait(client, tv_payload(current=direction), "open_" + direction)
    position = terminal.owned()[0]
    if direction == "long": terminal.bid = position.sl - 0.01
    else: terminal.ask = position.sl + 0.01
    terminal.positions.remove(position)  # Simulate broker-side SL fill.
    count = len(terminal.requests)
    post_and_wait(client, tv_payload(direction, "flat", alertMessage="__STOPLOSS"), "close_" + direction)
    assert len(terminal.requests) == count and not terminal.owned()


def test_emergency_stop_can_be_disabled(webhook_app):
    client, terminal = webhook_app
    client.app.state.trading_service.workers["A"].gateway.emergency_sl_distance = 0
    post_and_wait(client, tv_payload(), "open_long")
    assert "sl" not in terminal.requests[-1]


def test_failed_close_keeps_position_and_reports_failure(webhook_app):
    client, terminal = webhook_app
    post_and_wait(client, tv_payload(), "open_long")
    ticket = terminal.owned()[0].ticket
    terminal.reject_orders = True
    post_and_wait(client, tv_payload("long", "flat"), "close_long", status="failed")
    assert [p.ticket for p in terminal.owned()] == [ticket]
    assert len(client.get("/api/trade-orders").json()) == 1


def test_algorithm_trading_off_blocks_order_send(webhook_app):
    client, terminal = webhook_app
    terminal.trade_allowed = False
    failed = post_and_wait(client, tv_payload(), "open_long", status="failed")
    assert "算法交易未开启" in failed["error"]
    assert not terminal.requests


def test_saved_volume_affects_next_open_but_close_uses_position_volume(webhook_app):
    client, terminal = webhook_app
    assert client.put("/api/trading/clients/A/volume", json={"volume": 0.02}).status_code == 200
    post_and_wait(client, tv_payload(), "open_long")
    assert terminal.owned()[0].volume == 0.02
    assert client.put("/api/trading/clients/A/volume", json={"volume": 0.03}).status_code == 200
    post_and_wait(client, tv_payload("long", "flat"), "close_long")
    assert terminal.requests[-1]["volume"] == 0.02
    post_and_wait(client, tv_payload(current="short"), "open_short")
    assert terminal.owned()[0].volume == 0.03


def test_disabled_client_and_market_closed(webhook_app):
    client, terminal = webhook_app
    assert client.post("/api/trading/clients/A/disable").status_code == 200
    post_and_wait(client, tv_payload(), "open_long", status="blocked")
    assert not terminal.requests
    assert client.post("/api/trading/clients/A/enable").status_code == 200
    terminal.reject_orders = True
    failed = post_and_wait(client, tv_payload(), "open_long", status="failed")
    assert "10018" in failed["error"]
    assert not terminal.owned() and client.get("/api/trade-orders").json() == []


@pytest.mark.parametrize("changes,code", [
    ({"name": "OTHER"}, 400), ({"symbol": "EURUSD"}, 400),
    ({"timestamp": "2000-01-01T00:00:00Z"}, 400), ({"timestamp": str(time.time() + 3600)}, 400),
    ({"timestamp": "not-a-date"}, 400), ({"marketPosition": "flat"}, 400),
    ({"prevMarketPosition": "long", "marketPosition": "long"}, 400),
    ({"name": None}, 422),
])
def test_invalid_webhooks_do_not_send_orders(webhook_app, changes, code):
    client, terminal = webhook_app
    response = client.post("/api/webhooks/tradingview", json=tv_payload(**changes))
    assert response.status_code == code
    assert not terminal.requests and client.get("/api/trade-signals").json() == []


def test_malformed_json_does_not_send_orders(webhook_app):
    client, terminal = webhook_app
    response = client.post("/api/webhooks/tradingview", content="{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert not terminal.requests
