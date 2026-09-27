from __future__ import annotations

import os
from pathlib import Path

import MetaTrader5 as mt5
import pytest

from app.config import Settings
from app.mt5_gateway import Mt5Gateway


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    # Exercise the real dotenv loader without leaking loaded values to other tests.
    monkeypatch.setattr(os, "environ", {
        key: value for key, value in os.environ.items() if not key.startswith("MT5_")
    })
    monkeypatch.setattr("app.config.PROJECT_ROOT", tmp_path)
    return tmp_path / ".env"


@pytest.mark.parametrize("style", ["double", "single", "unquoted", "forward", "escaped"])
def test_copied_windows_paths_reach_each_terminal_unchanged(env_file, monkeypatch, style):
    paths = [
        r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe",
        r"C:\Program Files\Five Percent Online MetaTrader 5\terminal64.exe",
    ]
    def quoted(path):
        if style == "single":
            return f"'{path}'"
        if style == "unquoted":
            return path
        if style == "forward":
            path = path.replace("\\", "/")
        if style == "escaped":
            path = path.replace("\\", "\\\\")
        return f'"{path}"'

    env_file.write_text("\n".join(
        f"MT5_{key}_TERMINAL_PATH={quoted(path)}" for key, path in zip("AB", paths)
    ), encoding="utf-8")
    calls = []
    monkeypatch.setattr(mt5, "shutdown", lambda: None)
    monkeypatch.setattr(mt5, "initialize", lambda path, **kwargs: calls.append(path) or True)
    for client in Settings.from_env().clients():
        gateway = Mt5Gateway(terminal_path=client.terminal_path, symbol=client.symbol,
            volume=client.volume, max_volume=client.max_volume, magic=client.magic,
            deviation=client.deviation, emergency_sl_distance=client.emergency_sl_distance,
            demo_only=client.demo_only)
        gateway._ensure_initialized()
    assert calls == [str(Path(path)) for path in paths]
    # Repeated settings loads must not corrupt the already loaded environment.
    assert [c.terminal_path for c in Settings.from_env().clients()] == [Path(p) for p in paths]


@pytest.mark.parametrize("key", ["MT5_TERMINAL_PATH", "MT5_A_TERMINAL_PATH"])
def test_restores_all_dotenv_control_escapes_in_directory_names(env_file, key):
    path = r"C:\apps\broker\ftmo\new\release\terminal\version\terminal64.exe"
    env_file.write_text(f'{key}="{path}"\n', encoding="utf-8")
    assert Settings.from_env().clients()[0].terminal_path == Path(path)


def test_process_environment_takes_precedence_and_accepts_copy_as_path(env_file, monkeypatch):
    env_file.write_text('MT5_A_TERMINAL_PATH="C:\\ignored\\terminal64.exe"', encoding="utf-8")
    path = r"C:\Program Files\FTMO Global Markets MT5 Terminal\terminal64.exe"
    monkeypatch.setenv("MT5_A_TERMINAL_PATH", f'  "{path}"  ')
    assert Settings.from_env().clients()[0].terminal_path == Path(path)


def test_same_terminal_in_different_quote_styles_is_rejected(env_file):
    path = r"C:\Program Files\MT5\terminal64.exe"
    env_file.write_text(
        f'MT5_A_TERMINAL_PATH="{path}"\nMT5_B_TERMINAL_PATH=\'{path}\'\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="不同"):
        Settings.from_env()
