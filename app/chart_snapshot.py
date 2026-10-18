"""Sanitized chart inputs, independent of alert delivery credentials/compiled Pine code."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from app.alert_template import PRICE_INPUTS, SIGNAL_DISTANCE_INPUTS, SIGNAL_INPUTS
from app.errors import ValidationError


def chart_snapshot(template: dict[str, Any]) -> dict[str, Any]:
    payload = template.get("payload", template)
    conditions = payload.get("conditions") if isinstance(payload, dict) else None
    if not isinstance(conditions, list):
        raise ValidationError("警报未保存完整策略快照，无法安全回放")
    studies = [study for condition in conditions if isinstance(condition, dict)
               for study in (condition.get("series") or [])
               if isinstance(study, dict) and str(study.get("study", "")).startswith("StrategyScript@")]
    if len(studies) != 1:
        raise ValidationError("警报缺少唯一的策略快照，不能安全回放")
    study = studies[0]
    inputs = {k: v for k, v in study.get("inputs", {}).items() if re.fullmatch(r"in_\d+", k)}
    required = {"in_0", "in_1", "in_2"} | {k for p in PRICE_INPUTS for k in p}
    required |= set(SIGNAL_DISTANCE_INPUTS.values()) | {k for p in SIGNAL_INPUTS.values() for k in p}
    if not required <= inputs.keys() or not study.get("pine_id") or not study.get("pine_version"):
        raise ValidationError("警报未保存完整的策略输入、脚本 ID 和版本，不能使用当前默认值替代历史参数")
    for value in inputs.values():
        if type(value) not in (str, int, float, bool) or (type(value) in (int, float) and not math.isfinite(value)):
            raise ValidationError("策略快照包含不支持的输入值")
    for enabled, price in PRICE_INPUTS:
        if type(inputs[enabled]) is not bool or type(inputs[price]) not in (int, float):
            raise ValidationError("策略价格参数格式不兼容")
        if not inputs[enabled]:
            inputs[price] = 0
    symbol = payload.get("symbol", "")
    try:
        decoded = json.loads(symbol.removeprefix("="))
        if isinstance(decoded, dict):
            symbol = decoded.get("symbol", "")
    except (ValueError, AttributeError):
        pass
    resolution = str(payload.get("resolution") or payload["conditions"][0].get("resolution", ""))
    if not isinstance(symbol, str) or not symbol or not resolution:
        raise ValidationError("警报缺少品种或周期，不能安全回放")
    return {"schema": 1, "pine_id": str(study["pine_id"]), "pine_version": str(study["pine_version"]),
            "symbol": symbol, "resolution": resolution, "inputs": inputs, "price_capacity": 20}
