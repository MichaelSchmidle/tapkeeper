"""Offline operations by default; only `run` contacts Telegram."""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import Config, Store


def private_path(value):
    path = Path(value).expanduser().resolve()
    checkout = Path(__file__).resolve().parent.parent
    if (checkout / "AGENTS.md").exists() and path.is_relative_to(checkout):
        raise ValueError("Private files must be outside checkout")
    return path


def load_token():
    if "TAPKEEPER_TOKEN" in os.environ:
        raise ValueError("Token must be supplied by file")
    token_file = os.environ.get("TAPKEEPER_TOKEN_FILE")
    if not token_file:
        raise ValueError("Token file is required")
    token = private_path(token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Token is required")
    return token


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "run", "status"):
        sub.add_parser(name)
    for name in ("export", "import-legacy", "import-v1", "backup", "restore"):
        sub.add_parser(name).add_argument("file")
    edit = sub.add_parser("set")
    edit.add_argument("date")
    edit.add_argument("slot")
    edit.add_argument("choice")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("prompt_id")
    args = parser.parse_args()
    store = None
    try:
        try:
            config = Config.load(private_path(args.config))
        except Exception:
            print(
                "Configuration rejected; check catalogue-only JSON, positive "
                "TAPKEEPER_USER_ID, TZ, TAPKEEPER_MORNING and "
                "TAPKEEPER_EVENING. See runtime runbook.",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        database = private_path(args.db)
        if args.command == "restore":
            Store.restore(private_path(args.file), database)
            print("Restored to new database; Telegram disabled.")
            return
        store = Store(database, config)
        now = datetime.now(timezone.utc)
        if args.command == "run":
            from .adapter import run

            # No library/network exception logging: URLs may contain bot credentials.
            logging.disable(logging.CRITICAL)
            run(store, load_token())
        elif args.command == "export":
            with private_path(args.file).open(
                "x", encoding="utf-8", newline=""
            ) as output:
                output.write(store.export_csv())
        elif args.command in ("import-legacy", "import-v1"):
            with private_path(args.file).open(encoding="utf-8", newline="") as source:
                store.import_csv(source.read(), now)
        elif args.command == "backup":
            store.backup(private_path(args.file))
        elif args.command == "set":
            store.backfill(args.date, args.slot, args.choice, now)
        elif args.command == "reconcile":
            with store.connection:
                result = store.connection.execute(
                    "UPDATE prompts SET state='reconciled' WHERE id=? AND state='uncertain'",
                    (args.prompt_id,),
                )
                if result.rowcount != 1:
                    raise ValueError("Not uncertain")
        elif args.command == "status":
            for row in store.connection.execute(
                "SELECT state,count(*) FROM prompts GROUP BY state"
            ):
                print(f"{row[0]}: {row[1]}")
        print("OK")
    except Exception:
        print(
            "Operation failed; check configuration, input, storage and operator runbook.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
