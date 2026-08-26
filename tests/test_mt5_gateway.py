from __future__ import annotations

from types import SimpleNamespace

import MetaTrader5 as mt5

from app.mt5_gateway import Mt5Gateway, account_trade_mode_name


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
