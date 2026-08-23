from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import OrderItem, SignalItem


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

    def list_signals(self, limit: int = 100) -> list[SignalItem]:
        safe_limit = min(max(limit, 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT signal_id, source, action, status, symbol, error, received_at, executed_at
                FROM trade_signals
                WHERE hidden_at IS NULL
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [SignalItem(**self._row_to_dict(row)) for row in rows]

    def clear_completed_signals(self) -> int:
        terminal_statuses = ("success", "failed", "blocked", "expired", "ignored")
        placeholders = ", ".join("?" for _ in terminal_statuses)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE trade_signals
                SET hidden_at = ?
                WHERE hidden_at IS NULL
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
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trade_orders
                    (signal_id, action, ticket, symbol, volume, price, retcode, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (signal_id, action, ticket, symbol, volume, price, retcode, message, utc_now()),
            )

    def list_orders(self, limit: int = 100) -> list[OrderItem]:
        safe_limit = min(max(limit, 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trade_orders ORDER BY id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [OrderItem(**self._row_to_dict(row)) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_file, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}
