from __future__ import annotations

from types import SimpleNamespace
from io import StringIO
from pathlib import Path

import MetaTrader5 as mt5
import pytest
from dotenv import dotenv_values

from app.mt5_gateway import Mt5Gateway, Mt5ExecutionError, account_trade_mode_name


@pytest.mark.parametrize("quote", ['"', "'"])
def test_windows_dotenv_terminal_path(monkeypatch, quote) -> None:
    path = r"C:\Program Files\Five Percent Online MetaTrader 5\terminal64.exe"
    parsed = dotenv_values(stream=StringIO(f"MT5_A_TERMINAL_PATH={quote}{path}{quote}"))
    gateway = Mt5Gateway(terminal_path=Path(parsed["MT5_A_TERMINAL_PATH"]),
        symbol="XAUUSD", volume=0.01, max_volume=0.1, magic=100, deviation=20,
        emergency_sl_distance=20, demo_only=False)
    monkeypatch.setattr(mt5, "shutdown", lambda: None)
    calls = []
    monkeypatch.setattr(mt5, "initialize", lambda actual, **kwargs: calls.append(actual) or True)
    if quote == '"':
        with pytest.raises(Mt5ExecutionError, match="单引号"):
            gateway._ensure_initialized()
        assert calls == []
    else:
        gateway._ensure_initialized()
        assert calls == [path]


def test_account_trade_mode_name_maps_mt5_modes() -> None:
    assert account_trade_mode_name(SimpleNamespace(trade_mode=mt5.ACCOUNT_TRADE_MODE_DEMO)) == "demo"
    assert account_trade_mode_name(SimpleNamespace(trade_mode=mt5.ACCOUNT_TRADE_MODE_CONTEST)) == "contest"
    assert account_trade_mode_name(SimpleNamespace(trade_mode=mt5.ACCOUNT_TRADE_MODE_REAL)) == "real"


def test_account_trade_mode_name_handles_missing_or_unknown_mode() -> None:
    assert account_trade_mode_name(None) == "unknown"
    assert account_trade_mode_name(SimpleNamespace(trade_mode=999)) == "unknown"
    assert account_trade_mode_name(SimpleNamespace()) == "unknown"


def test_order_filling_maps_fok_capability() -> None:
    assert Mt5Gateway._order_filling(SimpleNamespace(filling_mode=1)) == mt5.ORDER_FILLING_FOK


def test_order_filling_prefers_ioc_when_supported() -> None:
    assert Mt5Gateway._order_filling(SimpleNamespace(filling_mode=3)) == mt5.ORDER_FILLING_IOC


def test_order_filling_uses_return_without_flags() -> None:
    assert Mt5Gateway._order_filling(SimpleNamespace(filling_mode=0)) == mt5.ORDER_FILLING_RETURN


def test_readiness_accepts_volume_above_old_program_limit(monkeypatch) -> None:
    gateway = Mt5Gateway(terminal_path=None, symbol="XAUUSD", volume=0.5, max_volume=0.1,
        magic=100, deviation=20, emergency_sl_distance=20, demo_only=False)
    monkeypatch.setattr(gateway, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(mt5, "terminal_info", lambda: SimpleNamespace(connected=True, trade_allowed=True))
    monkeypatch.setattr(mt5, "account_info", lambda: SimpleNamespace(trade_allowed=True, trade_expert=True))
    monkeypatch.setattr(mt5, "symbol_select", lambda *args: True)
    monkeypatch.setattr(mt5, "symbol_info", lambda *args: SimpleNamespace(trade_mode=mt5.SYMBOL_TRADE_MODE_FULL))
    gateway._validate_trading_ready()
    assert gateway._normalize_volume(gateway.volume, SimpleNamespace(volume_min=0.01, volume_max=10, volume_step=0.01)) == 0.5
