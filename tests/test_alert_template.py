from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.alert_template import AlertTemplateBuilder, parse_prices
from app.errors import ValidationError


ROOT = Path(__file__).resolve().parent.parent


def strategy_inputs(document: dict) -> dict:
    return document["payload"]["conditions"][0]["series"][0]["inputs"]


def test_parse_prices_accepts_whitespace_and_normal_decimals() -> None:
    assert parse_prices("4600  4620.5\n4660") == [Decimal("4600"), Decimal("4620.5"), Decimal("4660")]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "至少"),
        ("1 2 3 4 5 6 7", "最多"),
        ("4600abc", "格式"),
        ("-1", "格式"),
        ("0", "大于 0"),
        ("4600 4600.0", "重复"),
    ],
)
def test_parse_prices_rejects_invalid_values(raw: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_prices(raw)


def test_builder_replaces_prices_switches_name_and_start_time() -> None:
    source_before = (ROOT / "payload.json").read_text(encoding="utf-8")
    builder = AlertTemplateBuilder(ROOT / "payload.json")

    result = builder.build(
        [Decimal("4600"), Decimal("4620.5"), Decimal("4660")],
        name="MT5_AU_QQ::GOLD_PRICE::test",
        webhook_url="http://127.0.0.1:8000/api/webhooks/tradingview",
        now_ms=1787505555000,
    )

    inputs = strategy_inputs(result)
    assert [(inputs[f"in_{index}"], inputs[f"in_{index + 1}"]) for index in (4, 7, 10)] == [
        (True, 4600),
        (True, 4620.5),
        (True, 4660),
    ]
    assert [(inputs[f"in_{index}"], inputs[f"in_{index + 1}"]) for index in (13, 16, 19)] == [
        (False, 0),
        (False, 0),
        (False, 0),
    ]
    assert inputs["in_2"] == 1787505480000
    assert result["payload"]["name"] == "MT5_AU_QQ::GOLD_PRICE::test"
    assert result["payload"]["web_hook"] == "http://127.0.0.1:8000/api/webhooks/tradingview"
    assert (ROOT / "payload.json").read_text(encoding="utf-8") == source_before
    json.dumps(result)
