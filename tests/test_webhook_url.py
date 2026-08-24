from __future__ import annotations

from starlette.requests import Request

from app.main import resolve_webhook_url


def make_request(*, scheme: str, host: str) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50000),
            "root_path": "",
            "path": "/api/trading/status",
            "query_string": b"",
            "headers": [(b"host", host.encode("ascii"))],
        }
    )


def test_webhook_url_uses_configured_value_first() -> None:
    request = make_request(scheme="http", host="10.0.0.8:8000")

    assert resolve_webhook_url(request, "https://trade.example.com/custom-hook") == "https://trade.example.com/custom-hook"


def test_webhook_url_uses_current_request_origin() -> None:
    request = make_request(scheme="https", host="trade.example.com")

    assert resolve_webhook_url(request, None) == "https://trade.example.com/api/webhooks/tradingview"
