"""Configuration must reject before opening storage or initializing Telegram."""

import json
import sys
from unittest.mock import Mock

import pytest

from tapkeeper import cli
from tapkeeper.core import Config, due_time

SETTINGS = {
    "TAPKEEPER_USER_ID": "1",
    "TAPKEEPER_CHAT_ID": "1",
    "TZ": "Europe/Zurich",
    "TAPKEEPER_MORNING": "10:00",
    "TAPKEEPER_EVENING": "20:00",
}


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    for key, value in SETTINGS.items():
        monkeypatch.setenv(key, value)
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps({"watches": {" exact.ID ": "Synthetic"}}))
    return path


def test_environment_is_the_only_scalar_source(catalogue):
    config = Config.load(catalogue)
    assert (config.user, config.chat) == (1, 1)
    assert (config.timezone, config.morning, config.evening) == (
        "Europe/Zurich",
        "10:00",
        "20:00",
    )
    assert config.watches == {" exact.ID ": "Synthetic"}
    assert due_time("2026-03-29", config.morning, config.timezone).isoformat() == (
        "2026-03-29T08:00:00+00:00"
    )
    assert due_time("2026-10-25", config.evening, config.timezone).isoformat() == (
        "2026-10-25T19:00:00+00:00"
    )


@pytest.mark.parametrize("key", SETTINGS)
@pytest.mark.parametrize("value", [None, "", "private-invalid-value"])
def test_bad_environment_precedes_side_effects(
    catalogue, monkeypatch, capsys, key, value
):
    if value is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, value)
    assert_cli_rejects(catalogue, monkeypatch, capsys)


def assert_cli_rejects(catalogue, monkeypatch, capsys):
    database = catalogue.parent / "state.db"
    monkeypatch.setattr(
        sys,
        "argv",
        ["tapkeeper", "--config", str(catalogue), "--db", str(database), "run"],
    )
    store = Mock()
    monkeypatch.setattr(cli, "Store", store)
    monkeypatch.setattr(
        "tapkeeper.adapter.run", Mock(side_effect=AssertionError("No network"))
    )
    token = Mock()
    monkeypatch.setattr(cli, "load_token", token)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    store.assert_not_called()
    token.assert_not_called()
    assert not database.exists()
    assert "private-invalid-value" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [
        {"user": 1},
        {"chat": 1},
        {"topic": None},
        {"timezone": "UTC"},
        {"morning": "08:00"},
        {"evening": "19:00"},
        {"unknown": True},
    ],
)
def test_stale_json_rejected(catalogue, monkeypatch, capsys, extra):
    catalogue.write_text(json.dumps({"watches": {"demo": "Demo"}, **extra}))
    assert_cli_rejects(catalogue, monkeypatch, capsys)


@pytest.mark.parametrize("value", ["0", "-1", "2", "1.0", "true", "+1", " 1", "١"])
def test_private_destination_required(catalogue, monkeypatch, value):
    monkeypatch.setenv("TAPKEEPER_CHAT_ID", value)
    with pytest.raises(ValueError):
        Config.load(catalogue)


def test_duplicate_catalogue_keys_rejected(catalogue):
    catalogue.write_text('{"watches":{"same.id":"One","same.id":"Two"}}')
    with pytest.raises(ValueError, match="Duplicate"):
        Config.load(catalogue)


def test_old_complete_json_rejected_before_store(catalogue, monkeypatch, capsys):
    catalogue.write_text(
        json.dumps(
            {
                "user": 1,
                "chat": 1,
                "topic": None,
                "timezone": "UTC",
                "morning": "08:00",
                "evening": "20:00",
                "watches": {"demo": "Demo"},
            }
        )
    )
    assert_cli_rejects(catalogue, monkeypatch, capsys)


@pytest.mark.parametrize("chat,topic", [(-1, None), (2, None), (1, 3)])
def test_constructor_rejects_group_or_topic(chat, topic):
    with pytest.raises((ValueError, TypeError)):
        Config(
            user=1,
            chat=chat,
            topic=topic,
            timezone="UTC",
            morning="10:00",
            evening="20:00",
            watches={"demo": "Demo"},
        )


@pytest.mark.parametrize("user,chat", [(1, -1), (1, 2), (True, 1), (1, True)])
def test_constructor_requires_matching_positive_integers(user, chat):
    with pytest.raises(ValueError):
        Config(
            user=user,
            chat=chat,
            timezone="UTC",
            morning="10:00",
            evening="20:00",
            watches={"demo": "Demo"},
        )


@pytest.mark.parametrize("clock", ["24:00", "1:00", "10:0٠", "10:00:00"])
def test_malformed_clock_rejected_before_storage(catalogue, monkeypatch, capsys, clock):
    monkeypatch.setenv("TAPKEEPER_MORNING", clock)
    assert_cli_rejects(catalogue, monkeypatch, capsys)
