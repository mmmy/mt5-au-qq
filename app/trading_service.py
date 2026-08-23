from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from app.errors import Mt5NotReadyError, TradingDisabledError, ValidationError
from app.models import (
    ManualActionResponse,
    Mt5Status,
    TradeAction,
    TradingRuntimeStatus,
    TradingToggleResponse,
    TradingViewWebhook,
    WebhookResponse,
)
from app.mt5_gateway import Mt5Gateway
from app.trade_repository import TradeRepository


POSITION_TRANSITIONS: dict[tuple[str, str], TradeAction] = {
    ("flat", "long"): TradeAction.OPEN_LONG,
    ("flat", "short"): TradeAction.OPEN_SHORT,
    ("long", "flat"): TradeAction.CLOSE_LONG,
    ("short", "flat"): TradeAction.CLOSE_SHORT,
    ("short", "long"): TradeAction.REVERSE_TO_LONG,
    ("long", "short"): TradeAction.REVERSE_TO_SHORT,
}
MANUAL_ACTIONS = {
    TradeAction.OPEN_LONG,
    TradeAction.OPEN_SHORT,
    TradeAction.CLOSE_LONG,
    TradeAction.CLOSE_SHORT,
}


def derive_trade_action(previous: str, current: str) -> TradeAction:
    transition = (previous.strip().lower(), current.strip().lower())
    action = POSITION_TRANSITIONS.get(transition)
    if action is None:
        raise ValidationError(f"不支持的仓位变化：{transition[0]} → {transition[1]}", code="UNSUPPORTED_POSITION_TRANSITION")
    return action


def webhook_signal_id(payload: TradingViewWebhook) -> str:
    identity = {
        "name": payload.name,
        "symbol": payload.symbol,
        "timestamp": payload.timestamp,
        "id": payload.order_id,
        "prev": payload.prev_market_position,
        "current": payload.market_position,
        "size": payload.size,
        "positionSize": payload.position_size,
    }
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class WorkItem:
    kind: str
    signal_id: str | None = None
    future: Future[Mt5Status] | None = None


class Mt5Worker:
    def __init__(
        self,
        repository: TradeRepository,
        gateway: Mt5Gateway,
        is_trading_enabled: Callable[[], bool],
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.is_trading_enabled = is_trading_enabled
        self._queue: queue.Queue[WorkItem] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="mt5-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(WorkItem(kind="stop"))
        if self._thread:
            self._thread.join(timeout=10)

    def enqueue(self, signal_id: str) -> None:
        self._queue.put(WorkItem(kind="signal", signal_id=signal_id))

    def get_status(self, timeout: float = 8) -> Mt5Status:
        future: Future[Mt5Status] = Future()
        self._queue.put(WorkItem(kind="status", future=future))
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            raise Mt5NotReadyError("读取 MT5 状态超时") from exc

    def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                item = self._queue.get()
                if item.kind == "stop":
                    break
                if item.kind == "status" and item.future:
                    item.future.set_result(self.gateway.status())
                    continue
                if item.kind == "signal" and item.signal_id:
                    self._execute_signal(item.signal_id)
        finally:
            self.gateway.shutdown()

    def _execute_signal(self, signal_id: str) -> None:
        signal = self.repository.get_signal(signal_id)
        if not signal or signal["status"] != "queued":
            return
        if not self.is_trading_enabled():
            self.repository.update_signal_status(signal_id, "blocked", error="交易执行未启用")
            return

        self.repository.update_signal_status(signal_id, "running")
        try:
            action = TradeAction(signal["action"])
            orders = self.gateway.execute(action)
            for order in orders:
                self.repository.add_order(
                    signal_id=signal_id,
                    action=order.action,
                    ticket=order.ticket,
                    symbol=order.symbol,
                    volume=order.volume,
                    price=order.price,
                    retcode=order.retcode,
                    message=order.message,
                )
            self.repository.update_signal_status(signal_id, "success")
        except Exception as exc:
            self.repository.update_signal_status(signal_id, "failed", error=str(exc)[:500])


class TradingService:
    def __init__(
        self,
        repository: TradeRepository,
        gateway: Mt5Gateway,
        *,
        webhook_url: str,
        symbol: str,
        volume: float,
        max_volume: float,
        emergency_sl_distance: float,
        demo_only: bool,
        signal_max_age_seconds: int,
        enabled_at_start: bool,
        strategy_name: str = "AU-BOT",
    ) -> None:
        self.repository = repository
        self.webhook_url = webhook_url
        self.symbol = symbol
        self.volume = volume
        self.max_volume = max_volume
        self.emergency_sl_distance = emergency_sl_distance
        self.demo_only = demo_only
        self.signal_max_age_seconds = signal_max_age_seconds
        self.strategy_name = strategy_name
        self._enabled = enabled_at_start
        self._enabled_lock = threading.Lock()
        self.worker = Mt5Worker(repository, gateway, self.is_enabled)

    def start(self) -> None:
        self.repository.initialize()
        self.worker.start()
        queued = self.repository.list_queued_signal_ids()
        if self.is_enabled():
            for signal_id in queued:
                self.worker.enqueue(signal_id)
        else:
            for signal_id in queued:
                self.repository.update_signal_status(signal_id, "blocked", error="服务启动时交易未启用")

    def stop(self) -> None:
        self.worker.stop()

    def is_enabled(self) -> bool:
        with self._enabled_lock:
            return self._enabled

    async def runtime_status(self) -> TradingRuntimeStatus:
        mt5_status = await asyncio.to_thread(self.worker.get_status)
        return TradingRuntimeStatus(
            enabled=self.is_enabled(),
            webhook_url=self.webhook_url,
            volume=self.volume,
            max_volume=self.max_volume,
            emergency_sl_distance=self.emergency_sl_distance,
            demo_only=self.demo_only,
            mt5=mt5_status,
        )

    async def enable(self) -> TradingToggleResponse:
        status = await asyncio.to_thread(self.worker.get_status)
        if status.error:
            raise Mt5NotReadyError(status.error)
        if not status.connected:
            raise Mt5NotReadyError("MT5 终端未连接")
        if not status.terminal_trade_allowed:
            raise Mt5NotReadyError("请先在 MT5 中开启算法交易")
        if not status.account_trade_allowed or not status.account_trade_expert:
            raise Mt5NotReadyError("当前账户不允许程序交易")
        if self.demo_only and not status.demo_account:
            raise Mt5NotReadyError("安全保护：当前只允许模拟账户")
        if not status.symbol_available:
            raise Mt5NotReadyError(f"找不到交易品种 {self.symbol}")
        with self._enabled_lock:
            self._enabled = True
        return TradingToggleResponse(enabled=True, message="交易执行已启用")

    def disable(self) -> TradingToggleResponse:
        with self._enabled_lock:
            self._enabled = False
        return TradingToggleResponse(enabled=False, message="交易执行已停止；已有持仓不会自动平仓")

    def ingest_webhook(self, payload: TradingViewWebhook) -> WebhookResponse:
        if payload.name != self.strategy_name:
            raise ValidationError("不是受支持的 TradingView 策略", code="UNSUPPORTED_STRATEGY")
        if payload.symbol.strip().upper() != self.symbol.upper():
            raise ValidationError(f"不支持的交易品种：{payload.symbol}", code="UNSUPPORTED_SYMBOL")
        self._validate_signal_age(payload.timestamp)
        action = derive_trade_action(payload.prev_market_position, payload.market_position)
        signal_id = webhook_signal_id(payload)
        body = payload.model_dump(by_alias=True, mode="json")
        body.pop("signalToken", None)
        enabled = self.is_enabled()
        status = "queued" if enabled else "blocked"
        error = None if enabled else "交易执行未启用"
        inserted = self.repository.insert_signal(
            signal_id=signal_id,
            source="tradingview",
            action=action.value,
            status=status,
            symbol=self.symbol,
            payload=body,
        )
        if not inserted:
            existing = self.repository.get_signal(signal_id)
            return WebhookResponse(
                accepted=existing is not None and existing["status"] not in {"failed", "expired"},
                duplicate=True,
                signal_id=signal_id,
                action=action.value,
                status=str(existing["status"] if existing else "unknown"),
            )
        if error:
            self.repository.update_signal_status(signal_id, status, error=error)
        else:
            self.worker.enqueue(signal_id)
        return WebhookResponse(
            accepted=enabled,
            duplicate=False,
            signal_id=signal_id,
            action=action.value,
            status=status,
        )

    def submit_manual_action(self, action: TradeAction) -> ManualActionResponse:
        if action not in MANUAL_ACTIONS:
            raise ValidationError("手动测试只支持开多、开空、平多、平空")
        if not self.is_enabled():
            raise TradingDisabledError()
        signal_id = "manual-" + uuid4().hex
        self.repository.insert_signal(
            signal_id=signal_id,
            source="manual",
            action=action.value,
            status="queued",
            symbol=self.symbol,
            payload={"action": action.value, "created_at": datetime.now(timezone.utc).isoformat()},
        )
        self.worker.enqueue(signal_id)
        return ManualActionResponse(accepted=True, signal_id=signal_id, action=action, status="queued")

    def _validate_signal_age(self, raw_timestamp: str) -> None:
        try:
            numeric = float(raw_timestamp)
        except (TypeError, ValueError) as exc:
            raise ValidationError("TradingView timestamp 格式不正确", code="INVALID_SIGNAL_TIMESTAMP") from exc
        timestamp_seconds = numeric / 1000 if numeric > 10_000_000_000 else numeric
        age = time.time() - timestamp_seconds
        if age > self.signal_max_age_seconds:
            raise ValidationError("TradingView 信号已经过期", code="EXPIRED_SIGNAL")
        if age < -60:
            raise ValidationError("TradingView 信号时间来自未来", code="INVALID_SIGNAL_TIMESTAMP")
