from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.models import OrderItem, SignalItem


TRADING_TIMEZONE = ZoneInfo("Asia/Shanghai")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradeRepository:
    def __init__(self, database_file: Path) -> None:
        self.database_file = database_file

    def initialize(self) -> None:
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS trade_signals (
                    signal_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    error TEXT,
                    received_at TEXT NOT NULL,
                    executed_at TEXT,
                    hidden_at TEXT
                );

                CREATE TABLE IF NOT EXISTS trade_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    ticket INTEGER,
                    symbol TEXT NOT NULL,
                    volume REAL NOT NULL,
                    price REAL,
                    retcode INTEGER,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES trade_signals(signal_id)
                );

                CREATE TABLE IF NOT EXISTS tv_alert_configs (
                    alert_id INTEGER PRIMARY KEY,
                    prices_json TEXT NOT NULL,
                    side TEXT NOT NULL,
                    valid_bars INTEGER NOT NULL,
                    start_time_ms INTEGER NOT NULL,
                    end_time_ms INTEGER NOT NULL,
                    resolution TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_trade_signals_received_at
                    ON trade_signals(received_at DESC);
                CREATE INDEX IF NOT EXISTS idx_trade_orders_signal_id
                    ON trade_orders(signal_id);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(trade_signals)").fetchall()
            }
            if "hidden_at" not in columns:
                connection.execute("ALTER TABLE trade_signals ADD COLUMN hidden_at TEXT")
            if "client_id" not in columns:
                connection.execute("ALTER TABLE trade_signals ADD COLUMN client_id TEXT")
            if "parent_signal_id" not in columns:
                connection.execute("ALTER TABLE trade_signals ADD COLUMN parent_signal_id TEXT")
            alert_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tv_alert_configs)")}
            for column in ("signal_settings_json", "valid_hours"):
                if column not in alert_columns:
                    connection.execute(f"ALTER TABLE tv_alert_configs ADD COLUMN {column} TEXT")
            if "email" not in alert_columns:
                connection.execute("ALTER TABLE tv_alert_configs ADD COLUMN email INTEGER")
            order_columns = {row["name"] for row in connection.execute("PRAGMA table_info(trade_orders)")}
            if "client_id" not in order_columns:
                connection.execute("ALTER TABLE trade_orders ADD COLUMN client_id TEXT NOT NULL DEFAULT 'A'")

    def insert_fanout(self, *, signal_id: str, source: str, action: str, symbol: str,
                      payload: dict[str, Any], clients: list[tuple[str, str, bool]]) -> bool:
        """Persist the original signal and all client tasks in one transaction."""
        try:
            with self._connect() as connection:
                enabled = any(item[2] for item in clients)
                now = utc_now()
                connection.execute("""INSERT INTO trade_signals
                    (signal_id, source, action, status, symbol, payload_json, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (signal_id, source, action, "queued" if enabled else "blocked", symbol,
                     json.dumps(payload, ensure_ascii=False), now))
                for client_id, target_symbol, active in clients:
                    connection.execute("""INSERT INTO trade_signals
                        (signal_id, source, action, status, symbol, payload_json, received_at,
                         client_id, parent_signal_id, error)
                        VALUES (?, ?, ?, ?, ?, '{}', ?, ?, ?, ?)""",
                        (f"{signal_id}:{client_id}", source, action, "queued" if active else "blocked",
                         target_symbol, now, client_id, signal_id, None if active else "交易执行未启用"))
            return True
        except sqlite3.IntegrityError:
            return False

    def claim_signal(self, signal_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute("UPDATE trade_signals SET status='running' WHERE signal_id=? AND status='queued'",
                                      (signal_id,)).rowcount == 1

    def recover_client_tasks(self, client_ids: list[str]) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT signal_id, status, client_id FROM trade_signals WHERE parent_signal_id IS NOT NULL AND status IN ('queued','running')").fetchall()
        queued = []
        for row in rows:
            if row["status"] == "running":
                self.update_signal_status(row["signal_id"], "failed", error="服务中断，交易结果不明确；需核对终端，不会自动重试")
            elif row["client_id"] not in client_ids:
                self.update_signal_status(row["signal_id"], "blocked", error="客户端配置已移除")
            else:
                queued.append(row["signal_id"])
        return queued

    def insert_signal(
        self,
        *,
        signal_id: str,
        source: str,
        action: str,
        status: str,
        symbol: str,
        payload: dict[str, Any],
    ) -> bool:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO trade_signals
                        (signal_id, source, action, status, symbol, payload_json, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (signal_id, source, action, status, symbol, json.dumps(payload, ensure_ascii=False), utc_now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_signal(self, signal_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trade_signals WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def update_signal_status(self, signal_id: str, status: str, *, error: str | None = None) -> None:
        executed_at = utc_now() if status in {"success", "failed", "blocked", "expired", "ignored"} else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trade_signals
                SET status = ?, error = ?, executed_at = COALESCE(?, executed_at)
                WHERE signal_id = ?
                """,
                (status, error, executed_at, signal_id),
            )
            row = connection.execute("SELECT parent_signal_id FROM trade_signals WHERE signal_id=?", (signal_id,)).fetchone()
            if row and row["parent_signal_id"]:
                parent = row["parent_signal_id"]
                children = connection.execute("SELECT client_id, status, error FROM trade_signals WHERE parent_signal_id=?", (parent,)).fetchall()
                statuses = {child["status"] for child in children}
                aggregate = ("running" if "running" in statuses else "queued" if "queued" in statuses
                             else "success" if statuses == {"success"} else "partial" if "success" in statuses
                             else "failed" if "failed" in statuses else "expired" if "expired" in statuses else "blocked")
                errors = "; ".join(f"{child['client_id']}: {child['error']}" for child in children if child["error"])
                connection.execute("UPDATE trade_signals SET status=?, error=?, executed_at=? WHERE signal_id=?",
                    (aggregate, errors or None, None if aggregate in {"running", "queued"} else utc_now(), parent))

    def list_signals(self, limit: int = 100) -> list[SignalItem]:
        safe_limit = min(max(limit, 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT signal_id, source, action, status, symbol, error, received_at, executed_at, payload_json
                FROM trade_signals
                WHERE hidden_at IS NULL AND parent_signal_id IS NULL
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            items = []
            for row in rows:
                item = self._row_to_dict(row)
                raw_payload = item.pop("payload_json")
                item["trigger_price"] = None
                item["exit_reason"] = None
                if item["source"] == "tradingview":
                    try:
                        payload = json.loads(raw_payload)
                        if isinstance(payload, dict) and item["action"] in {"close_long", "close_short"}:
                            item["exit_reason"] = (
                                "止损" if payload.get("alertMessage") == "__STOPLOSS" else "止盈"
                            )
                        raw_price = payload.get("price") if isinstance(payload, dict) else None
                        if isinstance(raw_price, (str, int, float)) and not isinstance(raw_price, bool):
                            price = str(raw_price).strip()
                            number = Decimal(price)
                            if number.is_finite() and number > 0:
                                item["trigger_price"] = price
                    except (ValueError, TypeError, InvalidOperation):
                        pass
                elif item["action"] in {"close_long", "close_short"}:
                    # Manual close signals do not carry TradingView metadata; by the
                    # configured rule, every close that is not a stop loss is take profit.
                    item["exit_reason"] = "止盈"
                children = connection.execute("SELECT client_id, status, symbol, error FROM trade_signals WHERE parent_signal_id=? ORDER BY client_id", (item["signal_id"],)).fetchall()
                item["executions"] = [self._row_to_dict(child) for child in children]
                items.append(SignalItem(**item))
        return items

    def clear_completed_signals(self) -> int:
        terminal_statuses = ("success", "partial", "failed", "blocked", "expired", "ignored")
        placeholders = ", ".join("?" for _ in terminal_statuses)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE trade_signals
                SET hidden_at = ?
                WHERE hidden_at IS NULL
                  AND parent_signal_id IS NULL
                  AND status IN ({placeholders})
                """,
                (utc_now(), *terminal_statuses),
            )
        return max(cursor.rowcount, 0)

    def list_queued_signal_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT signal_id FROM trade_signals WHERE status = 'queued' ORDER BY received_at",
            ).fetchall()
        return [str(row["signal_id"]) for row in rows]

    def get_runtime_setting(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM runtime_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else None

    def set_runtime_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def add_order(
        self,
        *,
        signal_id: str,
        action: str,
        ticket: int | None,
        symbol: str,
        volume: float,
        price: float | None,
        retcode: int | None,
        message: str | None,
        client_id: str = "A",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_orders
                    (signal_id, action, ticket, symbol, volume, price, retcode, message, created_at, client_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (signal_id, action, ticket, symbol, volume, price, retcode, message, utc_now(), client_id),
            )

    def list_orders(self, limit: int = 100) -> list[OrderItem]:
        safe_limit = min(max(limit, 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trade_orders ORDER BY id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [OrderItem(**self._row_to_dict(row)) for row in rows]

    def trade_statistics(self, start_date: str, end_date: str, account: str | None = None) -> dict[str, Any]:
        """Return successful close-signal statistics for inclusive local dates."""
        first_day = date.fromisoformat(start_date)
        last_day = date.fromisoformat(end_date)
        start = datetime.combine(first_day, time.min, tzinfo=TRADING_TIMEZONE).astimezone(timezone.utc)
        end = datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=TRADING_TIMEZONE).astimezone(timezone.utc)
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH closes AS (
                    SELECT parent.signal_id, parent.action, parent.payload_json,
                           parent.received_at,
                           COALESCE(MIN(CASE WHEN child.status = 'success'
                               AND (? IS NULL OR child.client_id = ?) THEN child.executed_at END),
                               parent.executed_at, parent.received_at) AS executed_at,
                           COUNT(child.signal_id) AS child_count,
                           GROUP_CONCAT(CASE WHEN child.status = 'success' THEN child.client_id END) AS successful_clients
                    FROM trade_signals AS parent
                    LEFT JOIN trade_signals AS child ON child.parent_signal_id = parent.signal_id
                    WHERE parent.parent_signal_id IS NULL
                      AND parent.action IN ('close_long', 'close_short')
                      AND (parent.status IN ('success', 'partial') OR child.status = 'success')
                    GROUP BY parent.signal_id
                )
                SELECT * FROM closes
                WHERE julianday(executed_at) >= julianday(?) AND julianday(executed_at) < julianday(?)
                ORDER BY julianday(executed_at) DESC
                """,
                (account, account, start.isoformat(), end.isoformat()),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except (TypeError, ValueError):
                    payload = {}
                stop = isinstance(payload, dict) and payload.get("alertMessage") == "__STOPLOSS"
                try:
                    timestamp = datetime.fromisoformat(row["executed_at"] or row["received_at"])
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    day = timestamp.astimezone(TRADING_TIMEZONE).date().isoformat()
                except (TypeError, ValueError):
                    continue
                clients = sorted(set((row["successful_clients"] or "").split(",")) - {""})
                if not row["child_count"]:
                    clients = ["A"]  # Single-terminal records created before fanout migration.
                if row["child_count"] and not clients:
                    continue
                if account and account not in clients:
                    continue
                items.append({"signal_id": row["signal_id"], "day": day, "reason": "止损" if stop else "止盈", "clients": clients})
        days: list[str] = []
        cursor = first_day
        while cursor <= last_day:
            days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        daily = [{"date": day, "take_profit": sum(item["day"] == day and item["reason"] == "止盈" for item in items),
                  "stop_loss": sum(item["day"] == day and item["reason"] == "止损" for item in items), "net_profit": None} for day in days]
        return {
            "start_date": start_date, "end_date": end_date, "account": account,
            "net_profit": None, "profit_currency": None, "profit_available": False,
            "profit_status": "unavailable", "profit_by_currency": [],
            "take_profit_count": sum(item["reason"] == "止盈" for item in items),
            "stop_loss_count": sum(item["reason"] == "止损" for item in items),
            "total_signal_count": len(items), "daily": daily,
            "recent": items[:5], "recent_deals": [],
        }

    def close_order_reasons(self) -> dict[tuple[str, int], str]:
        """Match MT5 deal.order to the originating local close order, never by time/profit."""
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT orders.client_id, orders.ticket, COALESCE(parent.payload_json, signals.payload_json) AS payload_json
                FROM trade_orders AS orders
                JOIN trade_signals AS signals ON signals.signal_id = orders.signal_id
                LEFT JOIN trade_signals AS parent ON parent.signal_id = signals.parent_signal_id
                WHERE orders.action IN ('close', 'close_long', 'close_short') AND orders.ticket IS NOT NULL
            """).fetchall()
        result = {}
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (ValueError, TypeError):
                payload = {}
            result[(row["client_id"], int(row["ticket"]))] = (
                "止损" if isinstance(payload, dict) and payload.get("alertMessage") == "__STOPLOSS" else "止盈"
            )
        return result

    def upsert_alert_config(
        self,
        *,
        alert_id: int,
        prices: list[str],
        side: str,
        valid_bars: int,
        start_time_ms: int,
        end_time_ms: int,
        resolution: str,
        signal_settings: dict[str, Any] | None = None,
        valid_hours: str | None = None,
        email: bool | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tv_alert_configs
                    (alert_id, prices_json, side, valid_bars, start_time_ms, end_time_ms, resolution, created_at, signal_settings_json, valid_hours, email)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_id) DO UPDATE SET
                    prices_json = excluded.prices_json,
                    side = excluded.side,
                    valid_bars = excluded.valid_bars,
                    start_time_ms = excluded.start_time_ms,
                    end_time_ms = excluded.end_time_ms,
                    resolution = excluded.resolution,
                    signal_settings_json = COALESCE(excluded.signal_settings_json, tv_alert_configs.signal_settings_json),
                    valid_hours = COALESCE(excluded.valid_hours, tv_alert_configs.valid_hours),
                    email = COALESCE(excluded.email, tv_alert_configs.email)
                """,
                (
                    alert_id,
                    json.dumps(prices, ensure_ascii=False),
                    side,
                    valid_bars,
                    start_time_ms,
                    end_time_ms,
                    resolution,
                    utc_now(),
                    json.dumps(signal_settings, ensure_ascii=False) if signal_settings is not None else None,
                    valid_hours,
                    int(email) if email is not None else None,
                ),
            )

    def get_alert_configs(self, alert_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not alert_ids:
            return {}
        placeholders = ", ".join("?" for _ in alert_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tv_alert_configs WHERE alert_id IN ({placeholders})",
                alert_ids,
            ).fetchall()
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            item = self._row_to_dict(row)
            item["prices"] = json.loads(item.pop("prices_json"))
            snapshot = item.pop("signal_settings_json")
            item["signal_settings"] = json.loads(snapshot) if snapshot is not None else None
            result[int(item["alert_id"])] = item
        return result

    def delete_alert_config(self, alert_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM tv_alert_configs WHERE alert_id = ?", (alert_id,))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_file, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}
