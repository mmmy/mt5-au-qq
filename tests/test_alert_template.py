from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.alert_template import AlertTemplateBuilder, default_valid_bars, parse_prices, valid_bars_for_hours
from app.errors import TemplateError, ValidationError
from app.models import AlertSignalSettings


ROOT = Path(__file__).resolve().parent.parent


def strategy_inputs(document: dict) -> dict:
    return document["payload"]["conditions"][0]["series"][0]["inputs"]


def test_signal_settings_replace_only_enable_inputs() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")
    source = json.loads((ROOT / "payload.json").read_text(encoding="utf-8-sig"))
    original = strategy_inputs(source)
    changes = {
        "fractal": {"minute_5": False, "minute_2": True},
        "pinbar": {"minute_5": True, "minute_2": False},
        "pinbar_more": {"minute_5": False, "minute_2": True},
        "insidebar": {"minute_5": True, "minute_2": True},
        "engulfing": {"minute_5": False, "minute_2": False},
    }
    result = builder.build(
        [Decimal("4600")], name="signals-test", now_ms=1787505555000,
        signal_settings=AlertSignalSettings.model_validate(changes),
    )
    inputs = strategy_inputs(result)
    assert [inputs[f"in_{index}"] for index in (67, 68, 70, 71, 75, 76, 80, 81, 83, 84)] == [
        False, True, True, False, False, True, True, True, False, False,
    ]
    for index in (3, 6, 63, 64, 65, 66, 69, 72, 73, 74, 77, 78, 79, 82, 85, 86, 87, 88, 113):
        assert inputs[f"in_{index}"] == original[f"in_{index}"]
    assert json.loads((ROOT / "payload.json").read_text(encoding="utf-8-sig")) == source


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_signal_settings_reject_invalid_template_switch(tmp_path: Path, value: object) -> None:
    source = json.loads((ROOT / "payload.json").read_text(encoding="utf-8-sig"))
    strategy_inputs(source)["in_80"] = value
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(TemplateError, match="in_80"):
        AlertTemplateBuilder(path).signal_settings()


def test_signal_price_distances_read_defaults_and_write_correct_indices() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")
    source = json.loads((ROOT / "payload.json").read_text(encoding="utf-8-sig"))
    original = strategy_inputs(source)
    settings = builder.signal_settings()
    assert (settings.price_delta, settings.entry_delta, settings.stop_delta) == (
        original["in_64"], original["in_65"], original["in_66"],
    )
    settings.price_delta = 2.75
    settings.entry_delta = 0
    settings.stop_delta = 4.125
    result = builder.build(
        [Decimal("4600")], name="distances-test", now_ms=1787505555000, signal_settings=settings,
    )
    inputs = strategy_inputs(result)
    assert (inputs["in_64"], inputs["in_65"], inputs["in_66"]) == (2.75, 0, 4.125)
    assert inputs["in_63"] == original["in_63"]
    assert inputs["in_67"] == original["in_67"]
    assert json.loads((ROOT / "payload.json").read_text(encoding="utf-8-sig")) == source


@pytest.mark.parametrize("value", [None, -1, True, "2", float("inf"), float("nan")])
def test_signal_distances_reject_invalid_template_values(tmp_path: Path, value: object) -> None:
    source = json.loads((ROOT / "payload.json").read_text(encoding="utf-8-sig"))
    strategy_inputs(source)["in_66"] = value
    path = tmp_path / "payload.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(TemplateError, match="in_66"):
        AlertTemplateBuilder(path).signal_settings()


def test_parse_prices_accepts_common_separators_and_normal_decimals() -> None:
    assert parse_prices("4600  4620.5\n4660,4680，4700、4720;4740；4760\t4780") == [
        Decimal("4600"),
        Decimal("4620.5"),
        Decimal("4660"),
        Decimal("4680"),
        Decimal("4700"),
        Decimal("4720"),
        Decimal("4740"),
        Decimal("4760"),
        Decimal("4780"),
    ]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "至少"),
        (" ".join(str(value) for value in range(1, 22)), "最多"),
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
        name="MT5_AU::GOLD_PRICE::test",
        webhook_url="http://127.0.0.1:8000/api/webhooks/tradingview",
        now_ms=1787505555000,
    )

    inputs = strategy_inputs(result)
    assert [(inputs[f"in_{index}"], inputs[f"in_{index + 1}"]) for index in (4, 7, 10)] == [
        (True, 4600),
        (True, 4620.5),
        (True, 4660),
    ]
    assert all(
        (inputs[f"in_{index}"], inputs[f"in_{index + 1}"]) == (False, 0)
        for index in range(13, 62, 3)
    )
    assert inputs["in_2"] == 1787505480000
    assert result["payload"]["name"] == "MT5_AU::GOLD_PRICE::test"
    assert result["payload"]["web_hook"] == "http://127.0.0.1:8000/api/webhooks/tradingview"
    assert (ROOT / "payload.json").read_text(encoding="utf-8") == source_before
    json.dumps(result)


def test_builder_sets_email_notification_explicitly() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")

    enabled = builder.build([Decimal("4600")], name="email-on", email=True, now_ms=1787505555000)
    disabled = builder.build([Decimal("4600")], name="email-off", email=False, now_ms=1787505555000)

    assert enabled["payload"]["email"] is True
    assert disabled["payload"]["email"] is False


def test_builder_supports_twenty_prices() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")
    prices = [Decimal(value) for value in range(4600, 4620)]

    result = builder.build(prices, name="twenty-prices", now_ms=1787505555000)

    inputs = strategy_inputs(result)
    assert [inputs[f"in_{5 + index * 3}"] for index in range(20)] == list(range(4600, 4620))
    assert all(inputs[f"in_{4 + index * 3}"] is True for index in range(20))


def test_webhook_message_is_valid_and_contains_required_placeholders() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")

    message = json.loads(builder.webhook_message())

    assert message["side"] == "{{strategy.order.action}}"
    assert message["marketPosition"] == "{{strategy.market_position}}"
    assert message["prevMarketPosition"] == "{{strategy.prev_market_position}}"
    assert message["symbol"] == "{{ticker}}"
    assert message["timestamp"] == "{{timenow}}"
    assert message["id"] == "{{strategy.order.id}}"


def test_builder_accepts_utf8_bom(tmp_path: Path) -> None:
    payload_file = tmp_path / "payload.json"
    source = (ROOT / "payload.json").read_bytes()
    payload_file.write_bytes(b"\xef\xbb\xbf" + source)

    message = json.loads(AlertTemplateBuilder(payload_file).webhook_message())

    assert message["name"] == "AU-BOT"


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


@pytest.mark.parametrize(
    ("hours", "resolution", "bars"),
    [
        (Decimal("24"), "2", 720),
        (Decimal("24"), "5", 288),
        (Decimal("0.5"), "15", 2),
        (Decimal("1"), "240", 1),
        (Decimal("1.01"), "60", 2),
    ],
)
def test_valid_bars_for_hours_rounds_up(hours: Decimal, resolution: str, bars: int) -> None:
    assert valid_bars_for_hours(hours, resolution) == bars


def test_valid_bars_for_hours_rejects_more_than_ten_thousand_bars() -> None:
    with pytest.raises(ValidationError, match="10000"):
        valid_bars_for_hours(Decimal("200"), "1")


def test_builder_accepts_valid_hours() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")

    result = builder.build(
        [Decimal("4600")],
        name="hours-test",
        valid_hours=Decimal("24"),
        resolution="5",
        now_ms=1787582700000,
    )

    settings = builder.strategy_settings(result)
    assert settings.valid_bars == 288
    assert settings.end_time_ms == 1787669100000


def test_builder_rejects_valid_hours_and_valid_bars_together() -> None:
    builder = AlertTemplateBuilder(ROOT / "payload.json")

    with pytest.raises(ValidationError, match="不能同时"):
        builder.build(
            [Decimal("4600")],
            name="conflicting-duration-test",
            valid_hours=Decimal("24"),
            valid_bars=288,
            resolution="5",
            now_ms=1787582700000,
        )


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
