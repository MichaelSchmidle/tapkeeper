"""Safety boundaries for the host-side backup operator."""

import importlib.util
from io import StringIO
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qsl, urlsplit

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "backup.py"
spec = importlib.util.spec_from_file_location("backup_operator", SCRIPT)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def test_mount_requires_actual_network_filesystem_not_autofs(monkeypatch):
    monkeypatch.setattr(
        backup,
        "run",
        Mock(
            return_value='{"filesystems":[{"target":"/backup","source":"systemd-1","fstype":"autofs"}]}'
        ),
    )
    with pytest.raises(ValueError):
        backup.require_mount(
            {
                "repository": "/backup/repo",
                "mountpoint": "/backup",
                "mount_source": "//nas/Backup",
                "mount_type": "cifs",
            }
        )


def test_mount_checks_source_and_target(monkeypatch):
    cfg = {
        "repository": "/backup/repo",
        "mountpoint": "/backup",
        "mount_source": "//nas/Backup",
        "mount_type": "cifs",
    }
    monkeypatch.setattr(
        backup,
        "run",
        Mock(
            return_value='{"filesystems":[{"target":"/backup","source":"//other/Backup","fstype":"cifs"}]}'
        ),
    )
    with pytest.raises(ValueError):
        backup.require_mount(cfg)
    monkeypatch.setattr(
        backup,
        "run",
        Mock(
            return_value='{"filesystems":[{"target":"/backup","source":"systemd-1","fstype":"autofs"},{"target":"/backup","source":"//nas/Backup","fstype":"cifs"}]}'
        ),
    )
    backup.require_mount(cfg)


def test_retention_filters_and_groups_changing_staging_paths():
    args = backup.retention_args()
    assert args[args.index("--tag") + 1] == "tapkeeper-automated"
    assert args[args.index("--group-by") + 1] == "host,tags"
    assert "--prune" not in args
    assert args[args.index("--keep-daily") + 1] == "7"
    assert args[args.index("--keep-weekly") + 1] == "4"
    assert args[args.index("--keep-monthly") + 1] == "6"


def test_staging_removed_on_native_backup_failure(config, monkeypatch):
    (Path(config["data_dir"]) / "tapkeeper.db").write_text("synthetic-db")
    monkeypatch.setattr(backup.os, "chown", Mock())

    def run(args, **kwargs):
        if args[:2] == ["docker", "run"]:
            raise RuntimeError("secret")
        return "[]"

    monkeypatch.setattr(backup, "run", Mock(side_effect=run))
    with pytest.raises(RuntimeError):
        backup.create_snapshot(config, Path("unused"))
    assert backup.run.call_args.args[0][:3] == ["docker", "rm", "--force"]
    assert list(Path(config["stage_dir"]).iterdir()) == []


def test_failure_is_redacted_and_never_sends_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        backup, "operate", Mock(side_effect=RuntimeError("bot-token-and-private-data"))
    )
    heartbeat = Mock()
    monkeypatch.setattr(backup, "heartbeat", heartbeat)
    monkeypatch.setattr(
        backup,
        "load_config",
        Mock(return_value={"backup_heartbeat": "https://monitor/push/private"}),
    )
    assert backup.main(["--config", str(tmp_path / "config.json"), "backup"]) == 1
    assert "bot-token" not in capsys.readouterr().err
    heartbeat.assert_called_once_with("https://monitor/push/private", False)


def test_check_does_not_renew_backup_heartbeat(monkeypatch):
    monkeypatch.setattr(backup, "operate", Mock())
    monkeypatch.setattr(
        backup,
        "load_config",
        Mock(return_value={"backup_heartbeat": "backup", "check_heartbeat": "check"}),
    )
    heartbeat = Mock()
    monkeypatch.setattr(backup, "heartbeat", heartbeat)
    assert backup.main(["--config", "unused", "check"]) == 0
    heartbeat.assert_called_once_with("check", True)


@pytest.mark.parametrize("fragment", ["", "#fragment"])
@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize(
    "query",
    [
        "",
        "?status=up&msg=OK&ping=",
        "?status=down&status=up&msg=old&msg=older&ping=",
        "?token=a%2Bb%26c&tag=one&tag=two&empty=&status=down&msg=old",
    ],
)
def test_heartbeat_replaces_status_and_preserves_other_parameters(
    monkeypatch, success, query, fragment
):
    url = "https://monitor.example/api/push/synthetic" + query + fragment
    opener = Mock(return_value=StringIO('{"ok":true}'))
    monkeypatch.setattr(backup, "urlopen", opener)

    backup.heartbeat(url, success)

    opener.assert_called_once()
    assert opener.call_args.kwargs == {"timeout": 20}
    sent = urlsplit(opener.call_args.args[0])
    original = urlsplit(url)
    assert (sent.scheme, sent.netloc, sent.path, sent.fragment) == (
        original.scheme,
        original.netloc,
        original.path,
        original.fragment,
    )
    parameters = parse_qsl(sent.query, keep_blank_values=True)
    assert [value for key, value in parameters if key == "status"] == [
        "up" if success else "down"
    ]
    assert [value for key, value in parameters if key == "msg"] == [
        "OK" if success else "Backup operation failed"
    ]
    assert [pair for pair in parameters if pair[0] not in ("status", "msg")] == [
        pair
        for pair in parse_qsl(original.query, keep_blank_values=True)
        if pair[0] not in ("status", "msg")
    ]


def test_overlap_fails_before_work(tmp_path, monkeypatch):
    cfg = {"lock_file": str(tmp_path / "lock")}
    monkeypatch.setattr(backup, "require_mount", Mock())
    with backup.exclusive_lock(cfg["lock_file"]):
        with pytest.raises(BlockingIOError):
            backup.operate(cfg, Path("unused"), "check")
    backup.require_mount.assert_not_called()


def test_scheduled_run_requires_both_deadline_monitors(monkeypatch):
    monkeypatch.setattr(backup, "load_config", Mock(return_value={}))
    monkeypatch.setattr(backup, "operate", Mock())
    assert backup.main(["--config", "unused", "--scheduled", "backup"]) == 1
    backup.operate.assert_not_called()


@pytest.fixture
def config(tmp_path):
    cfg = {
        "repository": str(tmp_path / "mount" / "repo"),
        "mountpoint": str(tmp_path / "mount"),
        "mount_source": "//nas/Backup",
        "mount_type": "cifs",
        "password_file": str(tmp_path / "password"),
        "data_dir": str(tmp_path / "data"),
        "app_config": str(tmp_path / "app.json"),
        "env_file": str(tmp_path / "settings.env"),
        "token_file": str(tmp_path / "token"),
        "stage_dir": str(tmp_path / "stage"),
        "lock_file": str(tmp_path / "lock"),
        "image": "example@sha256:" + "a" * 64,
    }
    Path(cfg["stage_dir"]).mkdir()
    Path(cfg["data_dir"]).mkdir()
    Path(cfg["app_config"]).write_text("{}")
    Path(cfg["env_file"]).write_text(
        "TAPKEEPER_USER_ID=1\nTZ=Europe/Zurich\nTAPKEEPER_MORNING=10:00\nTAPKEEPER_EVENING=20:00\n"
    )
    Path(cfg["env_file"]).chmod(0o600)
    Path(cfg["token_file"]).write_text("synthetic-token")
    return cfg


def test_missing_database_fails_before_container_or_snapshot(config, monkeypatch):
    runner = Mock(return_value="[]")
    monkeypatch.setattr(backup, "run", runner)
    with pytest.raises(ValueError, match="Source database missing"):
        backup.create_snapshot(config, Path("unused"))
    assert len(runner.call_args_list) == 1
    assert runner.call_args.args[0][:3] == ["docker", "image", "inspect"]
    assert list(Path(config["stage_dir"]).iterdir()) == []


def test_snapshot_retention_and_cleanup_precede_success(config, tmp_path, monkeypatch):
    import json

    (Path(config["data_dir"]) / "tapkeeper.db").write_text("synthetic-db")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(backup.os, "chown", Mock())
    monkeypatch.setattr(backup, "run", Mock(return_value="[]"))
    calls = []

    def restic(cfg, *args):
        calls.append(args)
        if args[0] == "backup":
            payload = Path(args[-1])
            assert (payload / "telegram-token").read_text() == "synthetic-token"
            assert (
                payload / "backup-config.json"
            ).read_bytes() == config_path.read_bytes()
            assert (payload / "backup.py").exists()
            assert (payload / "settings.env").read_bytes() == Path(
                cfg["env_file"]
            ).read_bytes()
            assert (payload / "config.json").read_bytes() == Path(
                cfg["app_config"]
            ).read_bytes()

    monkeypatch.setattr(backup, "restic", restic)
    backup.create_snapshot(config, config_path)
    assert [c[0] for c in calls] == ["backup", "forget"]
    assert list(Path(config["stage_dir"]).iterdir()) == []
    docker_call = backup.run.call_args_list[1].args[0]
    assert docker_call[docker_call.index("--network") + 1] == "none"
    assert "--env-file" in docker_call
    assert "synthetic-token" not in str(docker_call)
    assert config["token_file"] not in str(docker_call)
    assert docker_call[-2:] == ["backup", "/backup/tapkeeper.db"]


@pytest.mark.parametrize("field", ["app_config", "env_file", "token_file"])
def test_config_drift_during_snapshot_fails_closed(
    config, tmp_path, monkeypatch, field
):
    (Path(config["data_dir"]) / "tapkeeper.db").write_text("synthetic-db")
    monkeypatch.setattr(backup.os, "chown", Mock())
    monkeypatch.setattr(backup, "restic", Mock())

    def run(args, **kwargs):
        if args[:2] == ["docker", "run"]:
            Path(config[field]).write_text("changed")
        return "[]"

    monkeypatch.setattr(backup, "run", run)
    (tmp_path / "unused").write_text("{}")
    with pytest.raises(ValueError, match="configuration changed"):
        backup.create_snapshot(config, tmp_path / "unused")
    backup.restic.assert_not_called()
    assert list(Path(config["stage_dir"]).iterdir()) == []


def test_config_rejects_staging_containing_sources(config, tmp_path):
    import json

    config["stage_dir"] = str(tmp_path)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Staging must be separate"):
        backup.load_config(path)


@pytest.mark.parametrize(
    "line",
    [
        "TAPKEEPER_TOKEN=synthetic-secret",
        "TAPKEEPER_TOKEN",
        "OTHER=secret",
        "TZ=UTC",
        "export TZ=UTC",
        "TZ=$HOST_TZ",
        'TZ="UTC"',
    ],
)
def test_env_file_rejects_secrets_inheritance_and_ambiguous_settings(config, line):
    path = Path(config["env_file"])
    path.write_text(path.read_text() + line + "\n")
    with pytest.raises(ValueError, match="environment file"):
        backup.read_env_file(path)


def test_env_file_accepts_single_identity_and_legacy_extra(config):
    path = Path(config["env_file"])
    assert b"TAPKEEPER_CHAT_ID" not in backup.read_env_file(path)
    path.write_text(path.read_text() + "TAPKEEPER_CHAT_ID=1\n")
    assert backup.read_env_file(path) == path.read_bytes()


def test_env_file_requires_private_permissions_and_explicit_scalars(config):
    path = Path(config["env_file"])
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        backup.read_env_file(path)
    path.chmod(0o600)
    path.write_text("TZ=Europe/Zurich\n")
    with pytest.raises(ValueError, match="Missing scalar"):
        backup.read_env_file(path)


def test_native_offline_backup_matches_catalogue_environment_and_all_tables(
    config, tmp_path, monkeypatch
):
    import json
    import os
    import sqlite3
    import subprocess
    import sys

    from tapkeeper.core import Config, Store
    from test_runtime import NOW

    catalogue = Path(config["app_config"])
    catalogue.write_text('{"watches":{" exact.ID ":"Synthetic"}}')
    source = Path(config["data_dir"]) / "tapkeeper.db"
    store = Store(source, Config.load(catalogue))
    prompt = store.ensure_prompt("2026-03-29", "morning")
    store.select("receipt", 1, 1, None, prompt["id"] + ":0", NOW)
    # Existing historical binding and version must survive the native snapshot.
    store.connection.execute("UPDATE prompts SET topic=3,chat=-100")
    store.connection.commit()
    store.close()
    operator_config = tmp_path / "operator.json"
    operator_config.write_text(json.dumps(config))
    monkeypatch.setattr(backup.os, "chown", Mock())

    def docker(args, **kwargs):
        if args[:2] != ["docker", "run"]:
            return "[]"
        assert args[args.index("--network") + 1] == "none"
        env_file = Path(args[args.index("--env-file") + 1])
        assert env_file.read_bytes() == Path(config["env_file"]).read_bytes()
        settings = dict(
            line.split("=", 1) for line in env_file.read_text().splitlines()
        )
        assert "TAPKEEPER_TOKEN" not in settings
        assert "TAPKEEPER_TOKEN_FILE" not in settings
        payload_mount = next(a for a in args if a.endswith(",dst=/backup"))
        payload = Path(
            payload_mount.removeprefix("type=bind,src=").removesuffix(",dst=/backup")
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tapkeeper.cli",
                "--config",
                str(catalogue),
                "--db",
                str(source),
                "backup",
                str(payload / "tapkeeper.db"),
            ],
            env={"PATH": os.environ["PATH"], **settings},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def tables(path):
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (1,)
            return {
                table: connection.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
                for table in ("watches", "records", "prompts", "callbacks")
            }

    def restic(cfg, *args):
        if args[0] == "backup":
            payload = Path(args[-1])
            assert tables(payload / "tapkeeper.db") == tables(source)
            assert (payload / "settings.env").read_bytes() == Path(
                cfg["env_file"]
            ).read_bytes()
            assert (payload / "config.json").read_bytes() == catalogue.read_bytes()
            assert (payload / "telegram-token").read_text() == "synthetic-token"

    monkeypatch.setattr(backup, "run", docker)
    monkeypatch.setattr(backup, "restic", restic)
    backup.create_snapshot(config, operator_config)
