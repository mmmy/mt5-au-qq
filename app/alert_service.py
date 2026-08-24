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
from app.trade_repository import TradeRepository
from app.tradingview import TradingViewClient


class AlertService:
    def __init__(
        self,
        client: TradingViewClient,
        template_builder: AlertTemplateBuilder,
        *,
        name_prefix: str,
        webhook_url: str | None,
        repository: TradeRepository | None = None,
    ) -> None:
        self.client = client
        self.template_builder = template_builder
        self.name_prefix = name_prefix
        self.webhook_url = webhook_url
        self.repository = repository
        self._mutation_lock = asyncio.Lock()

    async def list_alerts(self) -> list[AlertItem]:
        alerts = await self.client.list_alerts()
        project_alerts = [self._to_alert_item(item) for item in alerts if self._is_project_alert(item)]
        if self.repository:
            configs = self.repository.get_alert_configs([item.alert_id for item in project_alerts])
            project_alerts = [self._enrich_alert(item, configs.get(item.alert_id)) for item in project_alerts]
        project_alerts.sort(key=lambda item: item.create_time or "", reverse=True)
        return project_alerts

    async def create_alert(
        self,
        raw_prices: str,
        request_id: UUID,
        *,
        webhook_url: str | None = None,
        side: str = "自动",
        valid_bars: int | None = None,
        start_time_ms: int | None = None,
        resolution: str = "2",
    ) -> CreateAlertResponse:
        prices = parse_prices(raw_prices)
        price_strings = [decimal_to_string(price) for price in prices]
        name = self.name_prefix + request_id.hex

        async with self._mutation_lock:
            existing = await self._find_by_name(name)
            if existing is not None:
                if self.repository:
                    config = self.repository.get_alert_configs([existing.alert_id]).get(existing.alert_id)
                    existing = self._enrich_alert(existing, config)
                return CreateAlertResponse(created=False, prices=price_strings, alert=existing)

            effective_webhook_url = webhook_url or self.webhook_url
            if not effective_webhook_url:
                raise TemplateError("无法确定 TradingView webhook URL")
            template = self.template_builder.build(
                prices,
                name=name,
                webhook_url=effective_webhook_url,
                side=side,
                valid_bars=valid_bars,
                start_time_ms=start_time_ms,
                resolution=resolution,
            )
            settings = self.template_builder.strategy_settings(template)
            alert_id = await self.client.create_alert(template)
            if alert_id is None:
                created_alert = await self._find_by_name(name)
                if created_alert is None:
                    raise TradingViewError("TradingView 已接受请求，但没有返回可确认的警报 ID")
            else:
                created_alert = self._alert_from_template(alert_id, template)

            created_alert = created_alert.model_copy(
                update={
                    "prices": price_strings,
                    "side": settings.side,
                    "valid_bars": settings.valid_bars,
                    "start_time_ms": settings.start_time_ms,
                    "end_time_ms": settings.end_time_ms,
                }
            )
            if self.repository:
                self.repository.upsert_alert_config(
                    alert_id=created_alert.alert_id,
                    prices=price_strings,
                    side=settings.side,
                    valid_bars=settings.valid_bars,
                    start_time_ms=settings.start_time_ms,
                    end_time_ms=settings.end_time_ms,
                    resolution=settings.resolution,
                )

            return CreateAlertResponse(created=True, prices=price_strings, alert=created_alert)

    async def delete_alert(self, alert_id: int) -> DeleteAlertResponse:
        async with self._mutation_lock:
            alerts = await self.list_alerts()
            if not any(item.alert_id == alert_id for item in alerts):
                raise AlertNotFoundError()
            await self.client.delete_alerts([alert_id])
            if self.repository:
                self.repository.delete_alert_config(alert_id)
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
    def _enrich_alert(alert: AlertItem, config: dict[str, Any] | None) -> AlertItem:
        if not config:
            return alert
        return alert.model_copy(
            update={
                "prices": config.get("prices"),
                "side": config.get("side"),
                "valid_bars": config.get("valid_bars"),
                "start_time_ms": config.get("start_time_ms"),
                "end_time_ms": config.get("end_time_ms"),
                "resolution": str(config.get("resolution") or alert.resolution),
            }
        )

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
