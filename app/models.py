from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CreateAlertRequest(BaseModel):
    prices: str = Field(min_length=1, max_length=200)
    request_id: UUID = Field(default_factory=uuid4)


class AlertItem(BaseModel):
    alert_id: int
    name: str
    active: bool
    symbol: str
    resolution: str
    create_time: str | None = None
    last_fire_time: str | None = None


class CreateAlertResponse(BaseModel):
    created: bool
    prices: list[str]
    alert: AlertItem


class DeleteAlertResponse(BaseModel):
    deleted: bool
    alert_id: int


class HealthResponse(BaseModel):
    status: str
    cookie_configured: bool
    payload_configured: bool


class TradeAction(StrEnum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    REVERSE_TO_LONG = "reverse_to_long"
    REVERSE_TO_SHORT = "reverse_to_short"


class TradingViewWebhook(BaseModel):
    name: str
    side: str
    exchange: str = ""
    period: str = ""
    market_position: str = Field(alias="marketPosition")
    prev_market_position: str = Field(alias="prevMarketPosition")
    symbol: str
    price: str = ""
    timestamp: str
    size: str = ""
    position_size: str = Field(default="", alias="positionSize")
    order_id: str = Field(default="", alias="id")
    alert_message: str = Field(default="", alias="alertMessage")
    comment: str = ""
    qty_type: str = Field(default="", alias="qtyType")
    signal_token: str = Field(default="", alias="signalToken")


class WebhookResponse(BaseModel):
    accepted: bool
    duplicate: bool
    signal_id: str
    action: str
    status: str


class SignalItem(BaseModel):
    signal_id: str
    source: str
    action: str
    status: str
    symbol: str
    error: str | None = None
    received_at: str
    executed_at: str | None = None


class ClearSignalsResponse(BaseModel):
    cleared: int


class OrderItem(BaseModel):
    id: int
    signal_id: str
    action: str
    ticket: int | None = None
    symbol: str
    volume: float
    price: float | None = None
    retcode: int | None = None
    message: str | None = None
    created_at: str


class Mt5Status(BaseModel):
    initialized: bool
    connected: bool
    terminal_trade_allowed: bool
    account_trade_allowed: bool
    account_trade_expert: bool
    demo_account: bool
    server: str | None = None
    login_masked: str | None = None
    symbol: str
    symbol_available: bool
    bid: float | None = None
    ask: float | None = None
    owned_long_positions: int = 0
    owned_short_positions: int = 0
    error: str | None = None


class TradingRuntimeStatus(BaseModel):
    enabled: bool
    webhook_url: str
    volume: float
    max_volume: float
    emergency_sl_distance: float
    demo_only: bool
    mt5: Mt5Status


class TradingToggleResponse(BaseModel):
    enabled: bool
    message: str


class ManualActionResponse(BaseModel):
    accepted: bool
    signal_id: str
    action: TradeAction
    status: Literal["queued"]


class ErrorDetail(BaseModel):
    code: str
    message: str


JsonObject = dict[str, Any]
