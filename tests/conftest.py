"""Every test uses synthetic scalar settings, including subprocess CLI tests."""

import pytest


@pytest.fixture(autouse=True)
def synthetic_environment(monkeypatch):
    for key, value in {
        "TAPKEEPER_USER_ID": "1",
        "TAPKEEPER_CHAT_ID": "1",
        "TZ": "Europe/Zurich",
        "TAPKEEPER_MORNING": "10:00",
        "TAPKEEPER_EVENING": "20:00",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TAPKEEPER_TOKEN", raising=False)
    monkeypatch.delenv("TAPKEEPER_TOKEN_FILE", raising=False)
