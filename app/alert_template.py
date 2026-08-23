from __future__ import annotations

import json
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.errors import TemplateError, ValidationError


PRICE_INPUTS: tuple[tuple[str, str], ...] = (
    ("in_4", "in_5"),
    ("in_7", "in_8"),
    ("in_10", "in_11"),
    ("in_13", "in_14"),
    ("in_16", "in_17"),
    ("in_19", "in_20"),
)
DECIMAL_PATTERN = re.compile(r"\d+(?:\.\d+)?\Z")


def parse_prices(raw_prices: str) -> list[Decimal]:
    parts = raw_prices.strip().split()
    if not parts:
        raise ValidationError("请至少输入一个价格")
    if len(parts) > len(PRICE_INPUTS):
        raise ValidationError("当前策略最多支持 6 个价格")

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
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        template = self._load_template()
        payload = template.get("payload")
        if not isinstance(payload, dict):
            raise TemplateError("payload.json 缺少 payload 对象")

        inputs = self._find_strategy_inputs(payload)
        self._validate_price_inputs(inputs)

        for index, (enable_key, price_key) in enumerate(PRICE_INPUTS):
            enabled = index < len(prices)
            inputs[enable_key] = enabled
            inputs[price_key] = decimal_to_json_number(prices[index]) if enabled else 0

        payload["name"] = name
        if webhook_url:
            payload["web_hook"] = webhook_url
        self._update_start_time(payload, inputs, now_ms=now_ms)
        return template

    def _load_template(self) -> dict[str, Any]:
        try:
            data = json.loads(self.payload_file.read_text(encoding="utf-8"))
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
    def _update_start_time(payload: dict[str, Any], inputs: dict[str, Any], *, now_ms: int | None) -> None:
        if "in_2" not in inputs:
            raise TemplateError("模板缺少开始时间参数 in_2")
        current_ms = now_ms if now_ms is not None else time.time_ns() // 1_000_000
        resolution = payload.get("resolution")
        if not resolution:
            conditions = payload.get("conditions")
            if isinstance(conditions, list) and conditions and isinstance(conditions[0], dict):
                resolution = conditions[0].get("resolution")
        try:
            interval_ms = int(str(resolution)) * 60_000
        except (TypeError, ValueError):
            interval_ms = 60_000
        if interval_ms <= 0:
            interval_ms = 60_000
        inputs["in_2"] = current_ms // interval_ms * interval_ms
