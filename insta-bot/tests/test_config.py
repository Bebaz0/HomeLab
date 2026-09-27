import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(config, "_cache", None)


def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 9, 28, h, m)


@pytest.mark.parametrize("now,inside", [
    ("16:44", False), ("16:45", True), ("23:59", True), ("00:04", True), ("00:05", False),
    ("12:00", False),
])
def test_window_past_midnight(now, inside):
    assert config.in_window(at(now), "16:45", "00:05") is inside


def test_window_same_day_and_extra():
    assert config.in_window(at("10:00"), "09:00", "17:00")
    assert not config.in_window(at("17:00"), "09:00", "17:00")
    assert config.in_window(at("00:10"), "16:45", "00:05", end_extra=10)
    assert not config.in_window(at("00:15"), "16:45", "00:05", end_extra=10)


def test_load_defaults_and_merge():
    assert config.load() == config.DEFAULTS
    config.CONFIG.write_text('{"start": "18:00"}')
    cfg = config.load()
    assert cfg["start"] == "18:00" and cfg["replies"] == config.DEFAULTS["replies"]


def test_load_corrupt_falls_back():
    config.CONFIG.write_text("{nope")
    assert config.load() == config.DEFAULTS


def test_save_roundtrip():
    cfg, errors = config.validate({"replies": ["  bora  ", "siga"], "end": "01:30"})
    assert not errors
    config.save(cfg)
    assert config.load()["replies"] == ["bora", "siga"]
    assert config.load()["end"] == "01:30"


@pytest.mark.parametrize("update", [
    {"start": "25:00"}, {"end": "9:00"}, {"replies": []}, {"replies": [""]},
    {"replies": ["x" * 101]}, {"triggers": ["*"]}, {"enabled": "yes"},
    {"cooldown_minutes": -1}, {"cooldown_minutes": True}, {"nope": 1},
    {"triggers": ["a"] * 51},
])
def test_validate_rejects(update):
    _, errors = config.validate(update)
    assert errors
