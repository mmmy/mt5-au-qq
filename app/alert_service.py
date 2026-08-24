from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.alert_template import AlertTemplateBuilder, decimal_to_string, parse_prices
from app.errors import AlertNotFoundError, TemplateError, TradingViewError
from app.models import AlertItem, CreateAlertResponse, DeleteAlertResponse
from app.tradingview import TradingViewClient


class AlertService:
    def __init__(
        self,
        client: TradingViewClient,
        template_builder: AlertTemplateBuilder,
        *,
        name_prefix: str,
        webhook_url: str | None,
    ) -> None:
        self.client = client
        self.template_builder = template_builder
        self.name_prefix = name_prefix
        self.webhook_url = webhook_url
        self._mutation_lock = asyncio.Lock()

    async def list_alerts(self) -> list[AlertItem]:
        alerts = await self.client.list_alerts()
        project_alerts = [self._to_alert_item(item) for item in alerts if self._is_project_alert(item)]
        project_alerts.sort(key=lambda item: item.create_time or "", reverse=True)
        return project_alerts

    async def create_alert(
        self,
        raw_prices: str,
        request_id: UUID,
        *,
        webhook_url: str | None = None,
    ) -> CreateAlertResponse:
        prices = parse_prices(raw_prices)
        price_strings = [decimal_to_string(price) for price in prices]
        name = self.name_prefix + request_id.hex

        async with self._mutation_lock:
            existing = await self._find_by_name(name)
            if existing is not None:
                return CreateAlertResponse(created=False, prices=price_strings, alert=existing)

            effective_webhook_url = webhook_url or self.webhook_url
            if not effective_webhook_url:
                raise TemplateError("无法确定 TradingView webhook URL")
            template = self.template_builder.build(prices, name=name, webhook_url=effective_webhook_url)
            alert_id = await self.client.create_alert(template)
            if alert_id is None:
                created_alert = await self._find_by_name(name)
                if created_alert is None:
                    raise TradingViewError("TradingView 已接受请求，但没有返回可确认的警报 ID")
            else:
                created_alert = self._alert_from_template(alert_id, template)

            return CreateAlertResponse(created=True, prices=price_strings, alert=created_alert)

    async def delete_alert(self, alert_id: int) -> DeleteAlertResponse:
        async with self._mutation_lock:
            alerts = await self.list_alerts()
            if not any(item.alert_id == alert_id for item in alerts):
                raise AlertNotFoundError()
            await self.client.delete_alerts([alert_id])
        return DeleteAlertResponse(deleted=True, alert_id=alert_id)

    async def _find_by_name(self, name: str) -> AlertItem | None:
        alerts = await self.client.list_alerts()
        for item in alerts:
            if item.get("name") == name and self._is_project_alert(item):
                return self._to_alert_item(item)
        return None

    def _is_project_alert(self, item: dict[str, Any]) -> bool:
        name = item.get("name")
        return isinstance(name, str) and name.startswith(self.name_prefix)

    @staticmethod
    def _to_alert_item(item: dict[str, Any]) -> AlertItem:
        try:
            alert_id = int(item.get("alert_id"))
        except (TypeError, ValueError) as exc:
            raise TradingViewError("TradingView 警报数据缺少有效 ID") from exc
        return AlertItem(
            alert_id=alert_id,
            name=str(item.get("name") or ""),
            active=bool(item.get("active")),
            symbol=AlertService._normalize_symbol(item.get("symbol")),
            resolution=str(item.get("resolution") or ""),
            create_time=AlertService._optional_string(item.get("create_time")),
            last_fire_time=AlertService._optional_string(item.get("last_fire_time")),
        )

    @staticmethod
    def _normalize_symbol(raw_symbol: Any) -> str:
        if not isinstance(raw_symbol, str):
            return ""
        encoded = raw_symbol[1:] if raw_symbol.startswith("=") else raw_symbol
        try:
            parsed = json.loads(encoded)
        except json.JSONDecodeError:
            return raw_symbol
        if isinstance(parsed, dict) and isinstance(parsed.get("symbol"), str):
            return parsed["symbol"]
        return raw_symbol

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _alert_from_template(alert_id: int, template: dict[str, Any]) -> AlertItem:
        payload = template["payload"]
        resolution = payload.get("resolution")
        if not resolution:
            conditions = payload.get("conditions") or []
            if conditions:
                resolution = conditions[0].get("resolution")
        return AlertItem(
            alert_id=alert_id,
            name=str(payload.get("name") or ""),
            active=bool(payload.get("active", True)),
            symbol=AlertService._normalize_symbol(payload.get("symbol")),
            resolution=str(resolution or ""),
            create_time=datetime.now(timezone.utc).isoformat(),
            last_fire_time=None,
        )
