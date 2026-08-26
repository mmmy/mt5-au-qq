from __future__ import annotations

import pytest

from app.main import cache_control_for_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", "no-store"),
        ("/index.html", "no-store"),
        ("/api/trading/status", "no-store"),
        ("/static/app.js", "no-cache, must-revalidate"),
        ("/static/style.css", "no-cache, must-revalidate"),
        ("/favicon.ico", None),
    ],
)
def test_cache_control_for_path(path: str, expected: str | None) -> None:
    assert cache_control_for_path(path) == expected
