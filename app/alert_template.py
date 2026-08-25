from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.errors import TemplateError, ValidationError


PRICE_INPUTS: tuple[tuple[str, str], ...] = tuple(
    (f"in_{4 + index * 3}", f"in_{5 + index * 3}") for index in range(20)
)
PRICE_SEPARATOR_PATTERN = re.compile(r"[\s,，、;；]+")
DECIMAL_PATTERN = re.compile(r"\d+(?:\.\d+)?\Z")
REQUIRED_WEBHOOK_PLACEHOLDERS = {
    "side": "{{strategy.order.action}}",
    "marketPosition": "{{strategy.market_position}}",
    "prevMarketPosition": "{{strategy.prev_market_position}}",
    "symbol": "{{ticker}}",
    "timestamp": "{{timenow}}",
    "id": "{{strategy.order.id}}",
}
ALERT_SIDES = {"自动", "看多", "看空"}
RESOLUTION_MINUTES = {
    "1": 1,
    "2": 2,
    "3": 3,
    "5": 5,
    "15": 15,
    "30": 30,
    "60": 60,
    "120": 120,
    "240": 240,
}


@dataclass(frozen=True, slots=True)
class AlertStrategySettings:
    side: str
    valid_bars: int
    start_time_ms: int
    end_time_ms: int
    resolution: str


def default_valid_bars(resolution: str) -> int:
    minutes = RESOLUTION_MINUTES.get(resolution)
    if minutes is None:
        raise ValidationError(f"不支持的警报时间级别：{resolution}")
    return math.ceil(24 * 60 / minutes)


def valid_bars_for_hours(valid_hours: Decimal, resolution: str) -> int:
    minutes = RESOLUTION_MINUTES.get(resolution)
    if minutes is None:
        raise ValidationError(f"不支持的警报时间级别：{resolution}")
    if isinstance(valid_hours, bool):
        raise ValidationError("有效时长必须大于 0")
    try:
        hours = Decimal(str(valid_hours))
    except InvalidOperation as exc:
        raise ValidationError("有效时长格式不正确") from exc
    if not hours.is_finite() or hours <= 0:
        raise ValidationError("有效时长必须大于 0")
    bars = int((hours * 60 / minutes).to_integral_value(rounding=ROUND_CEILING))
    if bars > 10_000:
        raise ValidationError("有效时长过长，换算后 K 线数不能超过 10000")
    return bars


def parse_prices(raw_prices: str) -> list[Decimal]:
    parts = [part for part in PRICE_SEPARATOR_PATTERN.split(raw_prices.strip()) if part]
    if not parts:
        raise ValidationError("请至少输入一个价格")
    if len(parts) > len(PRICE_INPUTS):
        raise ValidationError(f"当前策略最多支持 {len(PRICE_INPUTS)} 个价格")

    prices: list[Decimal] = []
    seen: set[Decimal] = set()
    for part in parts:
        if not DECIMAL_PATTERN.fullmatch(part):
            raise ValidationError(f"价格格式不正确：{part}")
        try:
            price = Decimal(part)
        except InvalidOperation as exc:
            raise ValidationError(f"价格格式不正确：{part}") from exc
        if not price.is_finite() or price <= 0:
            raise ValidationError(f"价格必须大于 0：{part}")
        if price in seen:
            raise ValidationError(f"价格不能重复：{part}")
        seen.add(price)
        prices.append(price)
    return prices


def decimal_to_json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValidationError("价格数值过大") from exc
    if result == float("inf"):
        raise ValidationError("价格数值过大")
    return result


def decimal_to_string(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


class AlertTemplateBuilder:
    def __init__(self, payload_file: Path) -> None:
        self.payload_file = payload_file

    def build(
        self,
        prices: list[Decimal],
        *,
        name: str,
        webhook_url: str | None = None,
        side: str = "自动",
        valid_bars: int | None = None,
        valid_hours: Decimal | None = None,
        start_time_ms: int | None = None,
        resolution: str | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        template = self._load_template()
        payload = template.get("payload")
        if not isinstance(payload, dict):
            raise TemplateError("payload.json 缺少 payload 对象")

        inputs = self._find_strategy_inputs(payload)
        self._validate_price_inputs(inputs)
        self._validate_strategy_inputs(inputs)

        settings = self._resolve_strategy_settings(
            payload,
            side=side,
            valid_bars=valid_bars,
            valid_hours=valid_hours,
            start_time_ms=start_time_ms,
            resolution=resolution,
            now_ms=now_ms,
        )

        for index, (enable_key, price_key) in enumerate(PRICE_INPUTS):
            enabled = index < len(prices)
            inputs[enable_key] = enabled
            inputs[price_key] = decimal_to_json_number(prices[index]) if enabled else 0

        payload["name"] = name
        if webhook_url:
            payload["web_hook"] = webhook_url
        inputs["in_0"] = settings.side
        inputs["in_1"] = settings.valid_bars
        inputs["in_2"] = settings.start_time_ms
        payload["resolution"] = settings.resolution
        for condition in payload.get("conditions") or []:
            if isinstance(condition, dict):
                condition["resolution"] = settings.resolution
        return template

    def strategy_settings(self, template: dict[str, Any]) -> AlertStrategySettings:
        payload = template.get("payload")
        if not isinstance(payload, dict):
            raise TemplateError("payload.json 缺少 payload 对象")
        inputs = self._find_strategy_inputs(payload)
        self._validate_strategy_inputs(inputs)
        resolution = str(payload.get("resolution") or "")
        if not resolution:
            conditions = payload.get("conditions") or []
            if conditions and isinstance(conditions[0], dict):
                resolution = str(conditions[0].get("resolution") or "")
        minutes = RESOLUTION_MINUTES.get(resolution)
        if minutes is None:
            raise TemplateError(f"模板包含不支持的警报时间级别：{resolution}")
        start_time_ms = int(inputs["in_2"])
        valid_bars = int(inputs["in_1"])
        return AlertStrategySettings(
            side=str(inputs["in_0"]),
            valid_bars=valid_bars,
            start_time_ms=start_time_ms,
            end_time_ms=start_time_ms + valid_bars * minutes * 60_000,
            resolution=resolution,
        )

    def webhook_message(self) -> str:
        template = self._load_template()
        payload = template.get("payload")
        if not isinstance(payload, dict):
            raise TemplateError("payload.json 缺少 payload 对象")
        raw_message = payload.get("message")
        if not isinstance(raw_message, str) or not raw_message.strip():
            raise TemplateError("payload.json 缺少 TradingView 警报消息")
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise TemplateError("TradingView 警报消息不是有效 JSON") from exc
        if not isinstance(message, dict):
            raise TemplateError("TradingView 警报消息必须是 JSON 对象")
        invalid = [key for key, placeholder in REQUIRED_WEBHOOK_PLACEHOLDERS.items() if message.get(key) != placeholder]
        if invalid:
            raise TemplateError("TradingView 警报消息缺少必要占位符：" + ", ".join(invalid))
        return json.dumps(message, ensure_ascii=False, indent=2)

    def _load_template(self) -> dict[str, Any]:
        try:
            # utf-8-sig accepts regular UTF-8 and transparently strips the BOM
            # written by editors such as Windows Notepad.
            data = json.loads(self.payload_file.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise TemplateError(f"找不到警报模板：{self.payload_file}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise TemplateError("payload.json 无法读取或不是有效 JSON") from exc
        if not isinstance(data, dict):
            raise TemplateError("payload.json 顶层必须是对象")
        return data

    @staticmethod
    def _find_strategy_inputs(payload: dict[str, Any]) -> dict[str, Any]:
        conditions = payload.get("conditions")
        if not isinstance(conditions, list):
            raise TemplateError("模板不是受支持的 TradingView conditions 格式")

        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            series = condition.get("series")
            if not isinstance(series, list):
                continue
            for item in series:
                if not isinstance(item, dict):
                    continue
                study = item.get("study")
                inputs = item.get("inputs")
                if isinstance(study, str) and study.startswith("StrategyScript@") and isinstance(inputs, dict):
                    return inputs
        raise TemplateError("模板中找不到 StrategyScript 输入参数")

    @staticmethod
    def _validate_price_inputs(inputs: dict[str, Any]) -> None:
        missing = [key for pair in PRICE_INPUTS for key in pair if key not in inputs]
        if missing:
            raise TemplateError("模板缺少价格参数：" + ", ".join(missing))

    @staticmethod
    def _validate_strategy_inputs(inputs: dict[str, Any]) -> None:
        missing = [key for key in ("in_0", "in_1", "in_2") if key not in inputs]
        if missing:
            raise TemplateError("模板缺少策略参数：" + ", ".join(missing))

    @staticmethod
    def _resolve_strategy_settings(
        payload: dict[str, Any],
        *,
        side: str,
        valid_bars: int | None,
        valid_hours: Decimal | None,
        start_time_ms: int | None,
        resolution: str | None,
        now_ms: int | None,
    ) -> AlertStrategySettings:
        if side not in ALERT_SIDES:
            raise ValidationError(f"不支持的开仓方向：{side}")
        selected_resolution = str(resolution or payload.get("resolution") or "")
        if not selected_resolution:
            conditions = payload.get("conditions")
            if isinstance(conditions, list) and conditions and isinstance(conditions[0], dict):
                selected_resolution = str(conditions[0].get("resolution") or "")
        minutes = RESOLUTION_MINUTES.get(selected_resolution)
        if minutes is None:
            raise ValidationError(f"不支持的警报时间级别：{selected_resolution}")
        if valid_hours is not None and valid_bars is not None:
            raise ValidationError("有效时长和有效 K 线数不能同时提供")
        if valid_hours is not None:
            selected_bars = valid_bars_for_hours(valid_hours, selected_resolution)
        else:
            selected_bars = default_valid_bars(selected_resolution) if valid_bars is None else valid_bars
        if isinstance(selected_bars, bool) or not isinstance(selected_bars, int) or not 1 <= selected_bars <= 10_000:
            raise ValidationError("有效 K 线数必须是 1～10000 的整数")
        current_ms = now_ms if now_ms is not None else time.time_ns() // 1_000_000
        selected_start_ms = current_ms if start_time_ms is None else start_time_ms
        if isinstance(selected_start_ms, bool) or not isinstance(selected_start_ms, int) or selected_start_ms <= 0:
            raise ValidationError("开始时间格式不正确")
        interval_ms = minutes * 60_000
        aligned_start_ms = selected_start_ms // interval_ms * interval_ms
        end_time_ms = aligned_start_ms + selected_bars * interval_ms
        if end_time_ms <= current_ms:
            raise ValidationError("警报结束时间已经过去，请调整开始时间或有效时长")
        return AlertStrategySettings(
            side=side,
            valid_bars=selected_bars,
            start_time_ms=aligned_start_ms,
            end_time_ms=end_time_ms,
            resolution=selected_resolution,
        )
