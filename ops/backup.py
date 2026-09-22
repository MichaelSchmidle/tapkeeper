#!/usr/bin/env python3
"""Host-side Tapkeeper backups. Requires Docker, Restic and findmnt; no Telegram."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen
import uuid


TAG = "tapkeeper-automated"


def run(args, *, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=3600)
    if check and result.returncode:
        # Tool output can contain credentials or private filenames.
        raise RuntimeError("External command failed")
    return result.stdout


def load_config(path):
    cfg = json.loads(path.read_text())
    for field in (
        "repository",
        "mountpoint",
        "password_file",
        "data_dir",
        "app_config",
        "env_file",
        "token_file",
        "stage_dir",
        "lock_file",
    ):
        value = cfg[field]
        if (
            not isinstance(value, str)
            or not Path(value).is_absolute()
            or any(c in value for c in ",\n\r")
        ):
            raise ValueError("Invalid path")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", cfg["image"]):
        raise ValueError("Use an immutable image digest")
    if cfg["mount_type"] not in ("cifs", "nfs", "nfs4"):
        raise ValueError("Require a network filesystem")
    if (
        not Path(cfg["repository"])
        .resolve()
        .is_relative_to(Path(cfg["mountpoint"]).resolve())
    ):
        raise ValueError("Repository must be on the expected mount")
    stage = Path(cfg["stage_dir"]).resolve()
    for field in (
        "data_dir",
        "app_config",
        "env_file",
        "token_file",
        "repository",
        "password_file",
    ):
        source = Path(cfg[field]).resolve()
        if (
            stage == source
            or source.is_relative_to(stage)
            or stage.is_relative_to(source)
        ):
            raise ValueError("Staging must be separate from persistent sources")
    for field in ("backup_heartbeat", "check_heartbeat"):
        if cfg.get(field) and not cfg[field].startswith("https://"):
            raise ValueError("Heartbeat must use HTTPS")
    return cfg


@contextmanager
def exclusive_lock(path):
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def require_mount(cfg):
    entries = json.loads(run(["findmnt", "--json", "--target", cfg["repository"]])).get(
        "filesystems", []
    )
    if not any(
        entry.get("target") == cfg["mountpoint"]
        and entry.get("source") == cfg["mount_source"]
        and entry.get("fstype") == cfg["mount_type"]
        for entry in entries
    ):
        raise ValueError("Expected network mount is missing")


def restic(cfg, *args):
    require_mount(cfg)
    return run(
        [
            "restic",
            "--repo",
            cfg["repository"],
            "--password-file",
            cfg["password_file"],
            *args,
        ]
    )


def retention_args():
    return [
        "forget",
        "--tag",
        TAG,
        "--group-by",
        "host,tags",
        "--keep-daily",
        "7",
        "--keep-weekly",
        "4",
        "--keep-monthly",
        "6",
    ]


def read_env_file(path):
    """Validate a literal Docker env-file; Docker, not this operator, loads it."""
    path = Path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError("Environment file must be private")
    content = path.read_bytes()
    required = {
        "TAPKEEPER_USER_ID",
        "TAPKEEPER_CHAT_ID",
        "TZ",
        "TAPKEEPER_MORNING",
        "TAPKEEPER_EVENING",
    }
    allowed = required | {
        "TAPKEEPER_IMAGE",
        "TAPKEEPER_CONFIG_FILE",
        "TAPKEEPER_TOKEN_FILE",
        "TAPKEEPER_DATA_DIR",
    }
    seen = set()
    for line in content.decode("utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        # No shell expansion, quotes, implicit host inheritance or unknown keys.
        match = re.fullmatch(r"([A-Z_]+)=([^\s\"'$`#]+)", line)
        if not match or match[1] not in allowed or match[1] in seen:
            raise ValueError("Invalid Docker environment file")
        seen.add(match[1])
    if not required <= seen:
        raise ValueError("Missing scalar environment settings")
    return content


def create_snapshot(cfg, config_path):
    # A missing source must never silently become a successful empty backup.
    with tempfile.TemporaryDirectory(
        prefix="tapkeeper-", dir=cfg["stage_dir"]
    ) as temporary:
        stage = Path(temporary)
        image_info = run(["docker", "image", "inspect", cfg["image"]])
        source = Path(cfg["data_dir"]) / "tapkeeper.db"
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError("Source database missing")
        app_config = Path(cfg["app_config"]).read_bytes()
        environment = read_env_file(cfg["env_file"])
        token = Path(cfg["token_file"]).read_bytes()
        if not token.strip():
            raise ValueError("Missing recovery token")
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        # The published image runs as 10001. Its native Store also needs a writable
        # source data mount; configuration and the container root stay read-only.
        os.chown(stage, 10001, 10001)
        os.chown(payload, 10001, 10001)
        # Freeze Docker input so it matches the archived settings exactly.
        env_path = stage / "settings.env"
        env_path.write_bytes(environment)
        env_path.chmod(0o600)
        name = "tapkeeper-backup-" + uuid.uuid4().hex
        try:
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--read-only",
                    "--env-file",
                    str(env_path),
                    "--user",
                    "10001:10001",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--mount",
                    f"type=bind,src={cfg['data_dir']},dst=/data",
                    "--mount",
                    f"type=bind,src={cfg['app_config']},dst=/config.json,readonly",
                    "--mount",
                    f"type=bind,src={payload},dst=/backup",
                    cfg["image"],
                    "--config",
                    "/config.json",
                    "--db",
                    "/data/tapkeeper.db",
                    "backup",
                    "/backup/tapkeeper.db",
                ]
            )
        finally:
            run(["docker", "rm", "--force", name], check=False)
        if (
            app_config != Path(cfg["app_config"]).read_bytes()
            or environment != Path(cfg["env_file"]).read_bytes()
            or token != Path(cfg["token_file"]).read_bytes()
        ):
            raise ValueError("Recovery configuration changed during backup")
        (payload / "settings.env").write_bytes(environment)
        (payload / "config.json").write_bytes(app_config)
        (payload / "telegram-token").write_bytes(token)
        (payload / "image.json").write_text(image_info)
        (payload / "backup-config.json").write_bytes(config_path.read_bytes())
        shutil.copyfile(__file__, payload / "backup.py")
        for extra in cfg.get("recovery_files", []):
            path = Path(extra)
            if path.name in {p.name for p in payload.iterdir()}:
                raise ValueError("Duplicate recovery filename")
            shutil.copyfile(path, payload / path.name)
        restic(cfg, "backup", "--tag", TAG, str(payload))
        restic(cfg, *retention_args())
    # TemporaryDirectory cleanup completes BEFORE a success heartbeat.


def operate(cfg, config_path, mode):
    with exclusive_lock(cfg["lock_file"]):
        Path(cfg["repository"]).stat()  # Trigger automount without creating anything.
        require_mount(cfg)
        if mode == "backup":
            create_snapshot(cfg, config_path)
        elif mode == "retention-dry-run":
            print(restic(cfg, *retention_args(), "--dry-run"))
        else:
            if mode == "check":
                # Reclaim unreferenced data only under the same operator lock.
                restic(cfg, "prune")
            restic(cfg, "check", *(["--read-data"] if mode == "fullcheck" else []))


def heartbeat(url, success):
    if not url:
        return
    parts = urlsplit(url)
    # Generated push URLs already carry status/msg; send exactly one of each.
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in ("status", "msg")
    ]
    query.extend(
        [
            ("status", "up" if success else "down"),
            ("msg", "OK" if success else "Backup operation failed"),
        ]
    )
    with urlopen(
        urlunsplit(parts._replace(query=urlencode(query))),
        timeout=20,
    ) as response:
        body = json.load(response)
        if body.get("ok") is not True:
            raise RuntimeError("Heartbeat rejected")


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--scheduled", action="store_true", help="Require both deadline monitors"
    )
    parser.add_argument(
        "mode", choices=("backup", "check", "fullcheck", "retention-dry-run")
    )
    args = parser.parse_args(argv)
    url = None
    try:
        cfg = load_config(args.config)
        if args.mode != "retention-dry-run":
            url = cfg.get(
                "backup_heartbeat" if args.mode == "backup" else "check_heartbeat"
            )
        if args.scheduled and not all(
            cfg.get(key) for key in ("backup_heartbeat", "check_heartbeat")
        ):
            raise ValueError("Scheduled operation requires deadline monitors")
        operate(cfg, args.config, args.mode)
        heartbeat(url, True)
    except Exception:
        try:
            heartbeat(url, False)
        except Exception:
            pass
        print(
            "Tapkeeper backup operation failed; check mounts, storage and private configuration.",
            file=sys.stderr,
        )
        return 1
    print("Tapkeeper backup operation complete.")
    return 0


if __name__ == "__main__":

    def interrupted(signum, frame):
        raise RuntimeError("Backup interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    sys.exit(main())
