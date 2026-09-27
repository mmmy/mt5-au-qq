from pathlib import Path

from fastapi.testclient import TestClient

from app.alert_service import AlertService
from app.alert_template import AlertTemplateBuilder
from app.main import create_app
from app.trade_repository import TradeRepository


def test_signal_settings_api_persists_and_rejects_invalid_updates(tmp_path: Path) -> None:
    repository = TradeRepository(tmp_path / "settings.db")
    repository.initialize()
    builder = AlertTemplateBuilder(Path(__file__).resolve().parent.parent / "payload.json")
    app = create_app()
    app.state.alert_service = AlertService(
        None, builder, name_prefix="test", webhook_url=None, repository=repository,
    )  # type: ignore[arg-type]
    # No lifespan: these isolated endpoints need no MT5 processes or remote session.
    client = TestClient(app)
    defaults = client.get("/api/alerts/signal-settings")
    assert defaults.status_code == 200
    data = defaults.json()
    data["engulfing"]["minute_2"] = True
    data.update(price_delta=2.75, entry_delta=0, stop_delta=4.125)
    assert client.put("/api/alerts/signal-settings", json=data).json() == data
    assert client.get("/api/alerts/signal-settings").json() == data
    invalid = {**data, "fractal": {"minute_5": "true", "minute_2": False}}
    assert client.put("/api/alerts/signal-settings", json=invalid).status_code == 422
    assert client.put("/api/alerts/signal-settings", json={}).status_code == 422
    for field in ("price_delta", "entry_delta", "stop_delta"):
        for invalid_value in (-1, True, "2"):
            assert client.put("/api/alerts/signal-settings", json={**data, field: invalid_value}).status_code == 422
        assert client.put(
            "/api/alerts/signal-settings", content=json_with_nonfinite(data, field),
            headers={"Content-Type": "application/json"},
        ).status_code == 422
    assert client.get("/api/alerts/signal-settings").json() == data


def json_with_nonfinite(data: dict, field: str) -> str:
    import json
    return json.dumps({**data, field: float("inf")})
