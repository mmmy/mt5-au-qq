from __future__ import annotations

from pathlib import Path
import sqlite3

from app.trade_repository import TradeRepository


def test_alert_config_round_trip_and_delete(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "trading.db")
    repository.initialize()
    repository.upsert_alert_config(
        alert_id=123,
        prices=["4600", "4620.5"],
        side="看多",
        valid_bars=288,
        start_time_ms=1787582700000,
        end_time_ms=1787669100000,
        resolution="5",
        email=True,
    )

    config = repository.get_alert_configs([123])[123]
    assert config["prices"] == ["4600", "4620.5"]
    assert config["side"] == "看多"
    assert config["valid_bars"] == 288
    assert config["resolution"] == "5"
    assert config["email"] == 1

    repository.delete_alert_config(123)
    assert repository.get_alert_configs([123]) == {}


def test_old_alert_schema_migrates_without_inventing_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE tv_alert_configs (
            alert_id INTEGER PRIMARY KEY, prices_json TEXT NOT NULL, side TEXT NOT NULL,
            valid_bars INTEGER NOT NULL, start_time_ms INTEGER NOT NULL, end_time_ms INTEGER NOT NULL,
            resolution TEXT NOT NULL, created_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO tv_alert_configs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (12, '["4600"]', "自动", 720, 1000, 86401000, "2", "legacy"))
    repository = TradeRepository(database)
    repository.initialize()
    repository.initialize()
    record = repository.get_alert_configs([12])[12]
    assert record["prices"] == ["4600"]
    assert record["signal_settings"] is None and record["valid_hours"] is None and record["email"] is None
