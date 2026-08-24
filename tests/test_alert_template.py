from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.alert_template import AlertTemplateBuilder, default_valid_bars, parse_prices
from app.errors import TemplateError, ValidationError


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


def test_webhook_message_is_valid_and_contains_required_placeholders() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")

    message = json.loads(builder.webhook_message())

    assert message["side"] == "{{strategy.order.action}}"
    assert message["marketPosition"] == "{{strategy.market_position}}"
    assert message["prevMarketPosition"] == "{{strategy.prev_market_position}}"
    assert message["symbol"] == "{{ticker}}"
    assert message["timestamp"] == "{{timenow}}"
    assert message["id"] == "{{strategy.order.id}}"


def test_webhook_message_rejects_missing_placeholders(tmp_path: Path) -> None:
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(json.dumps({"payload": {"message": "{}"}}), encoding="utf-8")

    with pytest.raises(TemplateError, match="必要占位符"):
        AlertTemplateBuilder(payload_file).webhook_message()


@pytest.mark.parametrize(
    ("resolution", "bars"),
    [("1", 1440), ("2", 720), ("5", 288), ("15", 96), ("30", 48), ("60", 24), ("240", 6)],
)
def test_default_valid_bars_is_one_day(resolution: str, bars: int) -> None:
    assert default_valid_bars(resolution) == bars


def test_builder_replaces_strategy_settings_and_aligns_start_time() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")
    result = builder.build(
        [Decimal("4600")],
        name="settings-test",
        side="看多",
        valid_bars=288,
        start_time_ms=1787582831000,
        resolution="5",
        now_ms=1787582700000,
    )

    inputs = strategy_inputs(result)
    settings = builder.strategy_settings(result)
    assert inputs["in_0"] == "看多"
    assert inputs["in_1"] == 288
    assert inputs["in_2"] == 1787582700000
    assert result["payload"]["resolution"] == "5"
    assert all(condition["resolution"] == "5" for condition in result["payload"]["conditions"])
    assert settings.end_time_ms == 1787669100000


def test_builder_rejects_alert_that_has_already_expired() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")

    with pytest.raises(ValidationError, match="结束时间已经过去"):
        builder.build(
            [Decimal("4600")],
            name="expired-test",
            valid_bars=1,
            start_time_ms=1787580000000,
            resolution="5",
            now_ms=1787582700000,
        )
