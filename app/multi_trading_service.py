from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.config import Settings
from app.errors import Mt5NotReadyError, TradingDisabledError, ValidationError
from app.models import (ClientRuntimeStatus, ManualActionResponse, Mt5Status, TradeAction,
                        TradingRuntimeStatus, TradingToggleResponse, WebhookResponse)
from app.mt5_process import ProcessMt5Gateway
from app.trading_service import (MANUAL_ACTIONS, TRADING_ENABLED_SETTING, Mt5Worker,
                                 TradingService, derive_trade_action, webhook_signal_id)


class MultiTradingService(TradingService):
    def __init__(self, repository, settings: Settings, gateway_factory=ProcessMt5Gateway):
        self.configs = {c.client_id: c for c in settings.clients()}
        first = next(iter(self.configs.values()))
        super().__init__(repository, None, webhook_url=settings.local_webhook_url,
            symbol=settings.mt5_symbol, volume=first.volume, max_volume=first.max_volume,
            emergency_sl_distance=first.emergency_sl_distance, demo_only=first.demo_only,
            signal_max_age_seconds=settings.signal_max_age_seconds,
            enabled_at_start=settings.trading_enabled_at_start)
        self.client_enabled = {key: True for key in self.configs}
        self.workers = {key: Mt5Worker(repository, gateway_factory(config),
            lambda key=key: self.is_enabled() and self.client_enabled[key],
            client_id=key, signal_max_age_seconds=self.signal_max_age_seconds)
            for key, config in self.configs.items()}

    def start(self):
        self.repository.initialize()
        saved = self.repository.get_runtime_setting(TRADING_ENABLED_SETTING)
        self._set_enabled(self._enabled_at_start if saved is None else saved == "1")
        for key, worker in self.workers.items():
            saved = self.repository.get_runtime_setting(f"trading_enabled_{key}")
            self.client_enabled[key] = saved != "0"
            worker.start()
        for signal_id in self.repository.recover_client_tasks(list(self.configs)):
            key = self.repository.get_signal(signal_id)["client_id"]
            self.workers[key].enqueue(signal_id)
        # Legacy pending signals do not have a client assignment. Do not broadcast them.
        for signal_id in self.repository.list_queued_signal_ids():
            if self.repository.get_signal(signal_id)["parent_signal_id"] is None:
                if not any(self.repository.get_signal(f"{signal_id}:{key}") for key in self.configs):
                    self.repository.update_signal_status(signal_id, "blocked", error="旧版未分配客户端的信号，请核对后手动操作")

    def stop(self):
        for worker in self.workers.values():
            worker.stop()
        # A timed out terminal call must not leave an execution process behind.
        for worker in self.workers.values():
            worker.gateway.shutdown()

    async def _status(self, key):
        try:
            return await asyncio.to_thread(self.workers[key].get_status)
        except Exception as exc:
            return Mt5Status(initialized=False, connected=False, terminal_trade_allowed=False,
                account_trade_allowed=False, account_trade_expert=False, demo_account=False,
                symbol=self.configs[key].symbol, symbol_available=False, error=str(exc))

    async def runtime_status(self, *, webhook_url=None):
        statuses = await asyncio.gather(*(self._status(key) for key in self.configs))
        clients = [ClientRuntimeStatus(client_id=key, enabled=self.client_enabled[key],
            volume=config.volume, mt5=status) for (key, config), status in zip(self.configs.items(), statuses)]
        return TradingRuntimeStatus(enabled=self.is_enabled(), webhook_url=webhook_url or self.webhook_url or "",
            volume=self.volume, max_volume=self.max_volume, emergency_sl_distance=self.emergency_sl_distance,
            demo_only=self.demo_only, mt5=statuses[0], clients=clients)

    def _ready_error(self, key, status):
        if status.error:
            return status.error
        if not (status.connected and status.terminal_trade_allowed and status.account_trade_allowed
                and status.account_trade_expert and status.symbol_available):
            return "终端未就绪，请检查连接、算法交易权限和品种"
        if self.configs[key].demo_only and not status.demo_account:
            return "当前只允许模拟账户"
        return None

    async def enable(self):
        statuses = await asyncio.gather(*(self._status(key) for key in self.configs))
        ready = [key for key, status in zip(self.configs, statuses)
                 if self.client_enabled[key] and not self._ready_error(key, status)]
        if not ready:
            raise Mt5NotReadyError("没有已启用且就绪的 MT5 客户端")
        self._set_enabled(True)
        return TradingToggleResponse(enabled=True, message="交易总开关已启用；各客户端独立执行并记录结果")

    async def toggle_client(self, key, enabled):
        if key not in self.configs:
            raise ValidationError("未知 MT5 客户端")
        if enabled:
            error = self._ready_error(key, await self._status(key))
            if error:
                raise Mt5NotReadyError(error)
        self.repository.set_runtime_setting(f"trading_enabled_{key}", "1" if enabled else "0")
        self.client_enabled[key] = enabled
        return TradingToggleResponse(enabled=enabled, message=f"客户端 {key} 已{'启用' if enabled else '停止'}；已有持仓不会自动平仓")

    def _fanout(self, signal_id, source, action, payload, target=None):
        if target is not None and target not in self.configs:
            raise ValidationError("未知 MT5 客户端")
        clients = [(key, config.symbol, self.is_enabled() and self.client_enabled[key])
                   for key, config in self.configs.items() if target is None or target == key]
        inserted = self.repository.insert_fanout(signal_id=signal_id, source=source,
            action=action.value, symbol=self.symbol, payload=payload, clients=clients)
        if inserted:
            for key, _, active in clients:
                if active:
                    self.workers[key].enqueue(f"{signal_id}:{key}")
        return inserted, any(active for _, _, active in clients)

    def ingest_webhook(self, payload):
        if payload.name != self.strategy_name:
            raise ValidationError("不是受支持的 TradingView 策略", code="UNSUPPORTED_STRATEGY")
        if payload.symbol.strip().upper() != self.symbol.upper():
            raise ValidationError(f"不支持的交易品种：{payload.symbol}", code="UNSUPPORTED_SYMBOL")
        self._validate_signal_age(payload.timestamp)
        action = derive_trade_action(payload.prev_market_position, payload.market_position)
        signal_id = webhook_signal_id(payload)
        body = payload.model_dump(by_alias=True, mode="json")
        body.pop("signalToken", None)
        inserted, enabled = self._fanout(signal_id, "tradingview", action, body)
        existing = self.repository.get_signal(signal_id)
        return WebhookResponse(accepted=enabled if inserted else existing["status"] not in {"failed", "expired", "blocked"},
            duplicate=not inserted, signal_id=signal_id, action=action.value,
            status=("queued" if enabled else "blocked") if inserted else existing["status"])

    def submit_manual_action(self, action: TradeAction, client_id=None):
        if action not in MANUAL_ACTIONS:
            raise ValidationError("手动测试只支持开多、开空、平多、平空")
        if client_id is not None and client_id not in self.configs:
            raise ValidationError("未知 MT5 客户端")
        if not self.is_enabled() or not any(active for key, active in self.client_enabled.items()
                                            if client_id is None or key == client_id):
            raise TradingDisabledError()
        signal_id = "manual-" + uuid4().hex
        self._fanout(signal_id, "manual", action,
            {"action": action.value, "created_at": datetime.now(timezone.utc).isoformat()}, client_id)
        return ManualActionResponse(accepted=True, signal_id=signal_id, action=action, status="queued")
