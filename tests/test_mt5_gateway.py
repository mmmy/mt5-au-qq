from __future__ import annotations

from types import SimpleNamespace

import MetaTrader5 as mt5

from app.mt5_gateway import Mt5Gateway


def test_order_filling_maps_fok_capability() -> None:
    assert Mt5Gateway._order_filling(SimpleNamespace(filling_mode=1)) == mt5.ORDER_FILLING_FOK


def test_order_filling_prefers_ioc_when_supported() -> None:
    assert Mt5Gateway._order_filling(SimpleNamespace(filling_mode=3)) == mt5.ORDER_FILLING_IOC


def test_order_filling_uses_return_without_flags() -> None:
    assert Mt5Gateway._order_filling(SimpleNamespace(filling_mode=0)) == mt5.ORDER_FILLING_RETURN
