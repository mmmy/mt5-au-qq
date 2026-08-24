from __future__ import annotations

from pathlib import Path

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
    )

    config = repository.get_alert_configs([123])[123]
    assert config["prices"] == ["4600", "4620.5"]
    assert config["side"] == "看多"
    assert config["valid_bars"] == 288
    assert config["resolution"] == "5"

    repository.delete_alert_config(123)
    assert repository.get_alert_configs([123]) == {}
