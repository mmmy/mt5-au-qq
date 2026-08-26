from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.alert_service import AlertService
from app.alert_template import AlertTemplateBuilder
from app.config import Settings
from app.errors import AppError
from app.models import (
    AlertItem,
    ClearSignalsResponse,
    CreateAlertRequest,
    CreateAlertResponse,
    DeleteAlertResponse,
    HealthResponse,
    ManualActionResponse,
    OrderItem,
    SignalItem,
    TradeAction,
    TradingRuntimeStatus,
    TradingToggleResponse,
    TradingViewSetupResponse,
    TradingViewWebhook,
    WebhookResponse,
)
from app.mt5_gateway import Mt5Gateway
from app.trade_repository import TradeRepository
from app.tradingview import TradingViewClient
from app.trading_service import TradingService


def cache_control_for_path(path: str) -> str | None:
    if path == "/" or path == "/index.html" or path.startswith("/api/"):
        return "no-store"
    if path.startswith("/static/"):
        return "no-cache, must-revalidate"
    return None


def resolve_webhook_url(request: Request, configured_url: str | None) -> str:
    if configured_url:
        return configured_url
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/webhooks/tradingview"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repository = TradeRepository(app_settings.database_file)
        repository.initialize()
        client = TradingViewClient(
            app_settings.cookie_file,
            origin=app_settings.tradingview_origin,
            timeout_seconds=app_settings.request_timeout_seconds,
        )
        template_builder = AlertTemplateBuilder(app_settings.payload_file)
        app.state.alert_template_builder = template_builder
        app.state.alert_service = AlertService(
            client,
            template_builder,
            name_prefix=app_settings.alert_name_prefix,
            webhook_url=app_settings.local_webhook_url,
            repository=repository,
        )
        gateway = Mt5Gateway(
            terminal_path=app_settings.mt5_terminal_path,
            symbol=app_settings.mt5_symbol,
            volume=app_settings.mt5_volume,
            max_volume=app_settings.mt5_max_volume,
            magic=app_settings.mt5_magic,
            deviation=app_settings.mt5_deviation,
            emergency_sl_distance=app_settings.mt5_emergency_sl_distance,
            demo_only=app_settings.mt5_demo_only,
        )
        app.state.trading_service = TradingService(
            repository,
            gateway,
            webhook_url=app_settings.local_webhook_url,
            symbol=app_settings.mt5_symbol,
            volume=app_settings.mt5_volume,
            max_volume=app_settings.mt5_max_volume,
            emergency_sl_distance=app_settings.mt5_emergency_sl_distance,
            demo_only=app_settings.mt5_demo_only,
            signal_max_age_seconds=app_settings.signal_max_age_seconds,
            enabled_at_start=app_settings.trading_enabled_at_start,
        )
        app.state.trading_service.start()
        try:
            yield
        finally:
            app.state.trading_service.stop()

    app = FastAPI(title="MT5 AU QQ", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings

    @app.middleware("http")
    async def add_cache_control_header(request: Request, call_next):
        response = await call_next(request)
        cache_control = cache_control_for_path(request.url.path)
        if cache_control:
            response.headers["Cache-Control"] = cache_control
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, error: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": {"code": error.code, "message": error.message}},
        )

    @app.get("/api/alerts", response_model=list[AlertItem])
    async def list_alerts(request: Request) -> list[AlertItem]:
        return await request.app.state.alert_service.list_alerts()

    @app.post("/api/alerts", response_model=CreateAlertResponse)
    async def create_alert(data: CreateAlertRequest, request: Request) -> CreateAlertResponse:
        webhook_url = resolve_webhook_url(request, request.app.state.settings.local_webhook_url)
        return await request.app.state.alert_service.create_alert(
            data.prices,
            data.request_id,
            webhook_url=webhook_url,
            side=data.side,
            valid_bars=data.valid_bars,
            valid_hours=data.valid_hours,
            start_time_ms=data.start_time_ms,
            resolution=data.resolution,
        )

    @app.delete("/api/alerts/{alert_id}", response_model=DeleteAlertResponse)
    async def delete_alert(alert_id: int, request: Request) -> DeleteAlertResponse:
        return await request.app.state.alert_service.delete_alert(alert_id)

    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        current_settings: Settings = request.app.state.settings
        return HealthResponse(
            status="ok",
            cookie_configured=current_settings.cookie_file.is_file() and current_settings.cookie_file.stat().st_size > 0,
            payload_configured=current_settings.payload_file.is_file() and current_settings.payload_file.stat().st_size > 0,
        )

    @app.post(
        "/api/webhooks/tradingview",
        response_model=WebhookResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def tradingview_webhook(data: TradingViewWebhook, request: Request) -> WebhookResponse:
        return request.app.state.trading_service.ingest_webhook(data)

    @app.get("/api/tradingview/setup", response_model=TradingViewSetupResponse)
    async def tradingview_setup(request: Request) -> TradingViewSetupResponse:
        webhook_url = resolve_webhook_url(request, request.app.state.settings.local_webhook_url)
        message = request.app.state.alert_template_builder.webhook_message()
        return TradingViewSetupResponse(webhook_url=webhook_url, message=message)

    @app.get("/api/trading/status", response_model=TradingRuntimeStatus)
    async def trading_status(request: Request) -> TradingRuntimeStatus:
        webhook_url = resolve_webhook_url(request, request.app.state.settings.local_webhook_url)
        return await request.app.state.trading_service.runtime_status(webhook_url=webhook_url)

    @app.post("/api/trading/enable", response_model=TradingToggleResponse)
    async def enable_trading(request: Request) -> TradingToggleResponse:
        return await request.app.state.trading_service.enable()

    @app.post("/api/trading/disable", response_model=TradingToggleResponse)
    async def disable_trading(request: Request) -> TradingToggleResponse:
        return request.app.state.trading_service.disable()

    @app.post(
        "/api/mt5/actions/{action}",
        response_model=ManualActionResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def manual_mt5_action(action: TradeAction, request: Request) -> ManualActionResponse:
        return request.app.state.trading_service.submit_manual_action(action)

    @app.get("/api/trade-signals", response_model=list[SignalItem])
    async def trade_signals(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> list[SignalItem]:
        return request.app.state.trading_service.repository.list_signals(limit)

    @app.post("/api/trade-signals/clear", response_model=ClearSignalsResponse)
    async def clear_trade_signals(request: Request) -> ClearSignalsResponse:
        cleared = request.app.state.trading_service.repository.clear_completed_signals()
        return ClearSignalsResponse(cleared=cleared)

    @app.get("/api/trade-orders", response_model=list[OrderItem])
    async def trade_orders(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> list[OrderItem]:
        return request.app.state.trading_service.repository.list_orders(limit)

    app.mount("/static", StaticFiles(directory=app_settings.static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(app_settings.static_dir / "index.html")

    return app


app = create_app()
