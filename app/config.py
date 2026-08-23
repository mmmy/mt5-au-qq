from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MT5_TERMINAL = Path(r"D:\Program Files\MetaTrader 5\terminal64.exe")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    cookie_file: Path
    payload_file: Path
    static_dir: Path
    alert_name_prefix: str
    tradingview_origin: str
    request_timeout_seconds: float
    database_file: Path
    local_webhook_url: str
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

    @classmethod
    def from_env(cls) -> "Settings":
        terminal_path_raw = os.getenv("MT5_TERMINAL_PATH")
        if terminal_path_raw:
            terminal_path = Path(terminal_path_raw)
        elif DEFAULT_MT5_TERMINAL.is_file():
            terminal_path = DEFAULT_MT5_TERMINAL
        else:
            terminal_path = None
        return cls(
            cookie_file=Path(os.getenv("TV_COOKIE_FILE", PROJECT_ROOT / ".tv-cookie")),
            payload_file=Path(os.getenv("TV_PAYLOAD_FILE", PROJECT_ROOT / "payload.json")),
            static_dir=Path(os.getenv("STATIC_DIR", PROJECT_ROOT / "app" / "static")),
            alert_name_prefix=os.getenv("TV_ALERT_NAME_PREFIX", "MT5_AU_QQ::GOLD_PRICE::"),
            tradingview_origin=os.getenv("TV_ORIGIN", "https://cn.tradingview.com"),
            request_timeout_seconds=float(os.getenv("TV_REQUEST_TIMEOUT_SECONDS", "20")),
            database_file=Path(os.getenv("DATABASE_FILE", PROJECT_ROOT / "data" / "trading.db")),
            local_webhook_url=os.getenv(
                "TRADINGVIEW_WEBHOOK_URL",
                "http://127.0.0.1:8000/api/webhooks/tradingview",
            ),
            mt5_terminal_path=terminal_path,
            mt5_symbol=os.getenv("MT5_SYMBOL", "XAUUSD"),
            mt5_volume=float(os.getenv("MT5_VOLUME", "0.01")),
            mt5_max_volume=float(os.getenv("MT5_MAX_VOLUME", "0.10")),
            mt5_magic=int(os.getenv("MT5_MAGIC", "26082301")),
            mt5_deviation=int(os.getenv("MT5_DEVIATION", "20")),
            mt5_emergency_sl_distance=float(os.getenv("MT5_EMERGENCY_SL_DISTANCE", "20")),
            mt5_demo_only=_env_bool("MT5_DEMO_ONLY", True),
            signal_max_age_seconds=int(os.getenv("SIGNAL_MAX_AGE_SECONDS", "180")),
            trading_enabled_at_start=_env_bool("TRADING_ENABLED_AT_START", False),
        )
