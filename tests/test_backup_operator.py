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
        "token_file": str(tmp_path / "token"),
        "stage_dir": str(tmp_path / "stage"),
        "lock_file": str(tmp_path / "lock"),
        "image": "example@sha256:" + "a" * 64,
    }
    Path(cfg["stage_dir"]).mkdir()
    Path(cfg["data_dir"]).mkdir()
    Path(cfg["app_config"]).write_text("{}")
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

    monkeypatch.setattr(backup, "restic", restic)
    backup.create_snapshot(config, config_path)
    assert [c[0] for c in calls] == ["backup", "forget"]
    assert list(Path(config["stage_dir"]).iterdir()) == []
    docker_call = backup.run.call_args_list[1].args[0]
    assert docker_call[docker_call.index("--network") + 1] == "none"
    assert docker_call[-2:] == ["backup", "/backup/tapkeeper.db"]


def test_config_drift_during_snapshot_fails_closed(config, tmp_path, monkeypatch):
    (Path(config["data_dir"]) / "tapkeeper.db").write_text("synthetic-db")
    monkeypatch.setattr(backup.os, "chown", Mock())
    monkeypatch.setattr(backup, "restic", Mock())

    def run(args, **kwargs):
        if args[:2] == ["docker", "run"]:
            Path(config["app_config"]).write_text("changed")
        return "[]"

    monkeypatch.setattr(backup, "run", run)
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
