from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MT5_TERMINAL = Path(r"D:\Program Files\MetaTrader 5\terminal64.exe")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Mt5ClientConfig:
    client_id: str
    terminal_path: Path | None
    symbol: str
    volume: float
    max_volume: float
    magic: int
    deviation: int
    emergency_sl_distance: float
    demo_only: bool
    expected_login: int | None = None
    expected_server: str | None = None


@dataclass(frozen=True, slots=True)
class Settings:
    cookie_file: Path
    payload_file: Path
    static_dir: Path
    alert_name_prefix: str
    tradingview_origin: str
    request_timeout_seconds: float
    database_file: Path
    local_webhook_url: str | None
    mt5_terminal_path: Path | None
    mt5_symbol: str
    mt5_volume: float
    mt5_max_volume: float
    mt5_magic: int
    mt5_deviation: int
    mt5_emergency_sl_distance: float
    mt5_demo_only: bool
    signal_max_age_seconds: int
    trading_enabled_at_start: bool
    mt5_clients: tuple[Mt5ClientConfig, ...] = ()

    def clients(self) -> tuple[Mt5ClientConfig, ...]:
        if self.mt5_clients:
            return self.mt5_clients
        return (Mt5ClientConfig("A", self.mt5_terminal_path, self.mt5_symbol,
            self.mt5_volume, self.mt5_max_volume, self.mt5_magic, self.mt5_deviation,
            self.mt5_emergency_sl_distance, self.mt5_demo_only),)

    @classmethod
    def from_env(cls) -> "Settings":
        # Keep explicitly supplied process/system variables authoritative.
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        terminal_path_raw = os.getenv("MT5_TERMINAL_PATH")
        if terminal_path_raw:
            terminal_path = Path(terminal_path_raw)
        elif DEFAULT_MT5_TERMINAL.is_file():
            terminal_path = DEFAULT_MT5_TERMINAL
        else:
            terminal_path = None
        settings = cls(
            cookie_file=Path(os.getenv("TV_COOKIE_FILE", PROJECT_ROOT / ".tv-cookie")),
            payload_file=Path(os.getenv("TV_PAYLOAD_FILE", PROJECT_ROOT / "payload.json")),
            static_dir=Path(os.getenv("STATIC_DIR", PROJECT_ROOT / "app" / "static")),
            alert_name_prefix=os.getenv("TV_ALERT_NAME_PREFIX", "MT5_AU::GOLD_PRICE::"),
            tradingview_origin=os.getenv("TV_ORIGIN", "https://cn.tradingview.com"),
            request_timeout_seconds=float(os.getenv("TV_REQUEST_TIMEOUT_SECONDS", "20")),
            database_file=Path(os.getenv("DATABASE_FILE", PROJECT_ROOT / "data" / "trading.db")),
            local_webhook_url=os.getenv("TRADINGVIEW_WEBHOOK_URL") or None,
            mt5_terminal_path=terminal_path,
            mt5_symbol=os.getenv("MT5_SYMBOL", "XAUUSD"),
            mt5_volume=float(os.getenv("MT5_VOLUME", "0.01")),
            mt5_max_volume=float(os.getenv("MT5_MAX_VOLUME", "0.10")),
            mt5_magic=int(os.getenv("MT5_MAGIC", "26082301")),
            mt5_deviation=int(os.getenv("MT5_DEVIATION", "20")),
            mt5_emergency_sl_distance=float(os.getenv("MT5_EMERGENCY_SL_DISTANCE", "20")),
            mt5_demo_only=_env_bool("MT5_DEMO_ONLY", False),
            signal_max_age_seconds=int(os.getenv("SIGNAL_MAX_AGE_SECONDS", "180")),
            trading_enabled_at_start=_env_bool("TRADING_ENABLED_AT_START", False),
        )
        clients = []
        for client_id in ("A", "B"):
            path = os.getenv(f"MT5_{client_id}_TERMINAL_PATH", "").strip()
            if path:
                clients.append(Mt5ClientConfig(
                    client_id=client_id,
                    terminal_path=Path(path),
                    symbol=settings.mt5_symbol,
                    volume=settings.mt5_volume,
                    max_volume=settings.mt5_max_volume,
                    magic=settings.mt5_magic,
                    deviation=settings.mt5_deviation,
                    emergency_sl_distance=settings.mt5_emergency_sl_distance,
                    demo_only=settings.mt5_demo_only,
                ))
        if clients and clients[0].client_id != "A":
            raise ValueError("配置客户端 B 时必须同时配置 MT5_A_TERMINAL_PATH")
        if len(clients) == 2 and clients[0].terminal_path.resolve() == clients[1].terminal_path.resolve():
            raise ValueError("两个 MT5 客户端必须使用不同的终端路径")
        return replace(settings, mt5_clients=tuple(clients))
