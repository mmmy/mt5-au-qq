from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

from app.models import Mt5Status, TradeAction


# The MetaTrader5 Python package exposes ORDER_FILLING_* constants but some
# builds omit the SYMBOL_FILLING_* capability flags returned by symbol_info.
SYMBOL_FILLING_FOK_FLAG = 1
SYMBOL_FILLING_IOC_FLAG = 2
ACCOUNT_TRADE_MODE_NAMES = {
    mt5.ACCOUNT_TRADE_MODE_DEMO: "demo",
    mt5.ACCOUNT_TRADE_MODE_CONTEST: "contest",
    mt5.ACCOUNT_TRADE_MODE_REAL: "real",
}


def account_trade_mode_name(account: Any | None) -> str:
    if account is None:
        return "unknown"
    try:
        return ACCOUNT_TRADE_MODE_NAMES.get(int(account.trade_mode), "unknown")
    except (AttributeError, TypeError, ValueError):
        return "unknown"


class Mt5ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutedOrder:
    action: str
    ticket: int | None
    symbol: str
    volume: float
    price: float | None
    retcode: int | None
    message: str | None


class Mt5Gateway:
    def __init__(
        self,
        *,
        terminal_path: Path | None,
        symbol: str,
        volume: float,
        max_volume: float,
        magic: int,
        deviation: int,
        emergency_sl_distance: float,
        demo_only: bool,
    ) -> None:
        self.terminal_path = terminal_path
        self.symbol = symbol
        self.volume = volume
        self.max_volume = max_volume
        self.magic = magic
        self.deviation = deviation
        self.emergency_sl_distance = emergency_sl_distance
        self.demo_only = demo_only
        self._initialized = False

    def shutdown(self) -> None:
        if self._initialized:
            mt5.shutdown()
            self._initialized = False

    def status(self) -> Mt5Status:
        try:
            self._ensure_initialized()
            terminal = mt5.terminal_info()
            account = mt5.account_info()
            symbol_info = mt5.symbol_info(self.symbol)
            tick = mt5.symbol_info_tick(self.symbol) if symbol_info else None
            positions = self._owned_positions()
            login = str(account.login) if account else ""
            account_trade_mode = account_trade_mode_name(account)
            return Mt5Status(
                initialized=True,
                connected=bool(terminal and terminal.connected),
                terminal_trade_allowed=bool(terminal and terminal.trade_allowed),
                account_trade_allowed=bool(account and account.trade_allowed),
                account_trade_expert=bool(account and account.trade_expert),
                demo_account=account_trade_mode == "demo",
                account_trade_mode=account_trade_mode,
                server=account.server if account else None,
                login_masked=("*" * max(len(login) - 4, 0) + login[-4:]) if login else None,
                symbol=self.symbol,
                symbol_available=bool(symbol_info),
                bid=float(tick.bid) if tick else None,
                ask=float(tick.ask) if tick else None,
                owned_long_positions=sum(1 for position in positions if position.type == mt5.POSITION_TYPE_BUY),
                owned_short_positions=sum(1 for position in positions if position.type == mt5.POSITION_TYPE_SELL),
            )
        except Exception as exc:
            return Mt5Status(
                initialized=self._initialized,
                connected=False,
                terminal_trade_allowed=False,
                account_trade_allowed=False,
                account_trade_expert=False,
                demo_account=False,
                account_trade_mode="unknown",
                symbol=self.symbol,
                symbol_available=False,
                error=str(exc),
            )

    def execute(self, action: TradeAction) -> list[ExecutedOrder]:
        self._validate_trading_ready()
        if action == TradeAction.OPEN_LONG:
            return self._open(mt5.ORDER_TYPE_BUY)
        if action == TradeAction.OPEN_SHORT:
            return self._open(mt5.ORDER_TYPE_SELL)
        if action == TradeAction.CLOSE_LONG:
            return self._close(mt5.POSITION_TYPE_BUY)
        if action == TradeAction.CLOSE_SHORT:
            return self._close(mt5.POSITION_TYPE_SELL)
        if action == TradeAction.REVERSE_TO_LONG:
            return self._close(mt5.POSITION_TYPE_SELL) + self._open(mt5.ORDER_TYPE_BUY)
        if action == TradeAction.REVERSE_TO_SHORT:
            return self._close(mt5.POSITION_TYPE_BUY) + self._open(mt5.ORDER_TYPE_SELL)
        raise Mt5ExecutionError(f"不支持的交易动作：{action}")

    def _ensure_initialized(self) -> None:
        terminal = mt5.terminal_info() if self._initialized else None
        if terminal and terminal.connected:
            return
        self.shutdown()
        if self.terminal_path:
            initialized = mt5.initialize(str(self.terminal_path))
        else:
            initialized = mt5.initialize()
        if not initialized:
            code, message = mt5.last_error()
            raise Mt5ExecutionError(f"连接 MT5 失败：{code} {message}")
        self._initialized = True

    def _validate_trading_ready(self) -> None:
        self._ensure_initialized()
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        if not terminal or not terminal.connected:
            raise Mt5ExecutionError("MT5 终端未连接")
        if not terminal.trade_allowed:
            raise Mt5ExecutionError("MT5 算法交易未开启")
        if not account or not account.trade_allowed or not account.trade_expert:
            raise Mt5ExecutionError("当前账户不允许程序交易")
        if self.demo_only and account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise Mt5ExecutionError("安全保护：当前只允许模拟账户")
        if self.volume <= 0 or self.volume > self.max_volume:
            raise Mt5ExecutionError("下单手数超出程序限制")
        if not mt5.symbol_select(self.symbol, True):
            raise Mt5ExecutionError(f"无法选择交易品种 {self.symbol}")
        info = mt5.symbol_info(self.symbol)
        if not info or info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
            raise Mt5ExecutionError(f"交易品种 {self.symbol} 当前不可交易")

    def _owned_positions(self) -> list[Any]:
        positions = mt5.positions_get(symbol=self.symbol) or ()
        return [position for position in positions if int(position.magic) == self.magic]

    def _open(self, order_type: int) -> list[ExecutedOrder]:
        desired_position_type = mt5.POSITION_TYPE_BUY if order_type == mt5.ORDER_TYPE_BUY else mt5.POSITION_TYPE_SELL
        opposite_position_type = mt5.POSITION_TYPE_SELL if desired_position_type == mt5.POSITION_TYPE_BUY else mt5.POSITION_TYPE_BUY
        positions = self._owned_positions()
        if any(position.type == desired_position_type for position in positions):
            return []

        results = self._close(opposite_position_type)
        info = mt5.symbol_info(self.symbol)
        if not info:
            raise Mt5ExecutionError(f"找不到交易品种 {self.symbol}")
        volume = self._normalize_volume(self.volume, info)
        results.append(self._send_deal(order_type=order_type, volume=volume, position_ticket=None, action="open"))
        return results

    def _close(self, position_type: int) -> list[ExecutedOrder]:
        results: list[ExecutedOrder] = []
        for position in self._owned_positions():
            if position.type != position_type:
                continue
            close_type = mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
            results.append(
                self._send_deal(
                    order_type=close_type,
                    volume=float(position.volume),
                    position_ticket=int(position.ticket),
                    action="close",
                )
            )
        return results

    def _send_deal(self, *, order_type: int, volume: float, position_ticket: int | None, action: str) -> ExecutedOrder:
        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        if not info or not tick or tick.bid <= 0 or tick.ask <= 0:
            raise Mt5ExecutionError(f"无法获取 {self.symbol} 实时报价")

        is_buy = order_type == mt5.ORDER_TYPE_BUY
        price = float(tick.ask if is_buy else tick.bid)
        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": volume,
            "type": order_type,
            "price": round(price, int(info.digits)),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "mt5-au-qq",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._order_filling(info),
        }
        if position_ticket is not None:
            request["position"] = position_ticket
        elif self.emergency_sl_distance > 0:
            raw_sl = price - self.emergency_sl_distance if is_buy else price + self.emergency_sl_distance
            request["sl"] = round(raw_sl, int(info.digits))

        check = mt5.order_check(request)
        if check is None:
            code, message = mt5.last_error()
            raise Mt5ExecutionError(f"MT5 订单检查失败：{code} {message}")
        if int(check.retcode) != 0:
            raise Mt5ExecutionError(f"MT5 订单检查未通过：{check.retcode} {check.comment}")

        result = mt5.order_send(request)
        if result is None:
            code, message = mt5.last_error()
            raise Mt5ExecutionError(f"MT5 下单失败：{code} {message}")
        success_codes = {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL, mt5.TRADE_RETCODE_PLACED}
        if int(result.retcode) not in success_codes:
            raise Mt5ExecutionError(f"MT5 下单失败：{result.retcode} {result.comment}")

        ticket = int(result.order or result.deal) if (result.order or result.deal) else position_ticket
        return ExecutedOrder(
            action=action,
            ticket=ticket,
            symbol=self.symbol,
            volume=float(result.volume or volume),
            price=float(result.price or price),
            retcode=int(result.retcode),
            message=str(result.comment or ""),
        )

    @staticmethod
    def _normalize_volume(volume: float, info: Any) -> float:
        if volume < info.volume_min or volume > info.volume_max:
            raise Mt5ExecutionError(
                f"手数 {volume} 超出品种范围 {info.volume_min}～{info.volume_max}"
            )
        step = float(info.volume_step)
        normalized = round(round(volume / step) * step, 8) if step > 0 else volume
        if not math.isclose(normalized, volume, rel_tol=0, abs_tol=1e-8):
            raise Mt5ExecutionError(f"手数 {volume} 不符合最小步进 {step}")
        return normalized

    @staticmethod
    def _order_filling(info: Any) -> int:
        filling_mode = int(info.filling_mode)
        if filling_mode & SYMBOL_FILLING_IOC_FLAG:
            return mt5.ORDER_FILLING_IOC
        if filling_mode & SYMBOL_FILLING_FOK_FLAG:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN
