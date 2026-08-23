from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from app.errors import TradingViewError


LIST_ALERTS_URL = "https://pricealerts.tradingview.com/list_alerts"
CREATE_ALERT_URL = "https://pricealerts.tradingview.com/create_alert"
DELETE_ALERTS_URL = "https://pricealerts.tradingview.com/delete_alerts"


class TradingViewClient:
    def __init__(self, cookie_file: Path, *, origin: str, timeout_seconds: float = 20) -> None:
        self.cookie_file = cookie_file
        self.origin = origin.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def list_alerts(self) -> list[dict[str, Any]]:
        result = await self._post(LIST_ALERTS_URL, {})
        alerts = result.get("r")
        if isinstance(alerts, list):
            return [item for item in alerts if isinstance(item, dict)]
        self._raise_api_error(result, default_message="TradingView 未返回警报列表")

    async def create_alert(self, payload: dict[str, Any]) -> int | None:
        result = await self._post(CREATE_ALERT_URL, payload)
        if not self._is_success(result):
            self._raise_api_error(result, default_message="TradingView 创建警报失败")
        alert_id = result.get("alert_id")
        if alert_id is None and isinstance(result.get("r"), dict):
            alert_id = result["r"].get("alert_id")
        try:
            return int(alert_id) if alert_id is not None else None
        except (TypeError, ValueError):
            return None

    async def delete_alerts(self, alert_ids: list[int]) -> None:
        result = await self._post(DELETE_ALERTS_URL, {"payload": {"alert_ids": alert_ids}})
        if not self._is_success(result):
            self._raise_api_error(result, default_message="TradingView 删除警报失败")

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        cookie = self._read_cookie()
        headers = {
            "Cookie": cookie,
            "Origin": self.origin,
            "Referer": self.origin + "/",
            "X-Usenewauth": "true",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise TradingViewError("连接 TradingView 超时", code="TRADINGVIEW_TIMEOUT", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise TradingViewError("无法连接 TradingView") from exc

        if response.status_code in (401, 403):
            raise TradingViewError("TradingView Cookie 已失效，请更新 .tv-cookie", code="TV_SESSION_EXPIRED", status_code=401)
        if response.is_error:
            raise TradingViewError(f"TradingView 请求失败（HTTP {response.status_code}）")
        try:
            result = response.json()
        except json.JSONDecodeError as exc:
            raise TradingViewError("TradingView 返回了非 JSON 数据，Cookie 可能已经失效", code="TV_SESSION_EXPIRED", status_code=401) from exc
        if not isinstance(result, dict):
            raise TradingViewError("TradingView 返回格式不正确")
        return result

    def _read_cookie(self) -> str:
        try:
            cookie = self.cookie_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise TradingViewError("找不到 .tv-cookie 文件", code="TV_COOKIE_MISSING", status_code=503) from exc
        except OSError as exc:
            raise TradingViewError("无法读取 .tv-cookie 文件", code="TV_COOKIE_UNREADABLE", status_code=503) from exc
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        if not cookie or "sessionid=" not in cookie:
            raise TradingViewError(".tv-cookie 内容不完整", code="TV_COOKIE_INVALID", status_code=503)
        return cookie

    @staticmethod
    def _is_success(result: dict[str, Any]) -> bool:
        return result.get("s") == "ok" or result.get("m") == "success"

    @staticmethod
    def _raise_api_error(result: dict[str, Any], *, default_message: str) -> None:
        raw_message = result.get("m") or result.get("message")
        message = str(raw_message)[:200] if raw_message else default_message
        lowered = message.lower()
        if "auth" in lowered or "login" in lowered or "session" in lowered:
            raise TradingViewError("TradingView Cookie 已失效，请更新 .tv-cookie", code="TV_SESSION_EXPIRED", status_code=401)
        raise TradingViewError(message)
