"""Token files keep credentials out of container environment configuration."""

import json
import sys

import pytest

from tapkeeper.cli import load_token, main


def test_token_file_and_legacy_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPKEEPER_TOKEN", raising=False)
    token = tmp_path / "token"
    token.write_text("synthetic-token\n", encoding="utf-8")
    monkeypatch.setenv("TAPKEEPER_TOKEN_FILE", str(token))
    assert load_token() == "synthetic-token"
    monkeypatch.setenv("TAPKEEPER_TOKEN", "other-token")
    with pytest.raises(ValueError):
        load_token()
    monkeypatch.delenv("TAPKEEPER_TOKEN_FILE")
    assert load_token() == "other-token"


def test_missing_or_empty_token_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("TAPKEEPER_TOKEN", raising=False)
    monkeypatch.delenv("TAPKEEPER_TOKEN_FILE", raising=False)
    with pytest.raises(ValueError):
        load_token()
    token = tmp_path / "token"
    token.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("TAPKEEPER_TOKEN_FILE", str(token))
    with pytest.raises(ValueError):
        load_token()
    token.unlink()
    with pytest.raises(FileNotFoundError):
        load_token()


def test_cli_token_file_dispatch_and_redacted_failure(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "user": 1,
                "chat": 2,
                "topic": None,
                "timezone": "Europe/Zurich",
                "morning": "08:00",
                "evening": "20:00",
                "watches": {"demo.ref": "Demo"},
            }
        )
    )
    token = tmp_path / "synthetic-secret-path"
    token.write_text("synthetic-token\n")
    monkeypatch.delenv("TAPKEEPER_TOKEN", raising=False)
    monkeypatch.setenv("TAPKEEPER_TOKEN_FILE", str(token))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tapkeeper",
            "--config",
            str(config),
            "--db",
            str(tmp_path / "state.db"),
            "run",
        ],
    )
    received = []
    monkeypatch.setattr(
        "tapkeeper.adapter.run", lambda store, value: received.append(value)
    )
    monkeypatch.setattr("tapkeeper.cli.logging.disable", lambda level: None)
    main()
    assert received == ["synthetic-token"]
    token.unlink()
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert received == ["synthetic-token"]
    assert capsys.readouterr().err == (
        "Operation failed; check configuration, input, storage and operator runbook.\n"
    )
