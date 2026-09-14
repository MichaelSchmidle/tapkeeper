"""PR review regressions at the CLI and Telegram dispatch boundaries."""

import csv
import io
import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

from telegram import Update

from tapkeeper.adapter import application
from tapkeeper.core import FIELDS, Store
from test_commands import CommandRequest, command_update
from test_runtime import Fixture, NOW


class ReviewRegressions(Fixture):
    def test_cli_csv_preserves_embedded_line_endings(self):
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps(asdict(self.cfg)), encoding="utf-8")
        row = dict(
            zip(
                FIELDS,
                [
                    "2026-03-28",
                    "morning",
                    "demo.ref",
                    "Historical\r\nlabel",
                    "2026-03-28T12:00:00+00:00",
                    "legacy\rsource",
                ],
            )
        )
        text = io.StringIO(newline="")
        writer = csv.DictWriter(text, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(row)
        source = Path(self.tmp.name) / "input.csv"
        source.write_bytes(text.getvalue().encode())

        def cli(db, *args):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tapkeeper.cli",
                    "--config",
                    str(config),
                    "--db",
                    str(db),
                    *map(str, args),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        for command in ("import-legacy", "import-v1"):
            database = Path(self.tmp.name) / f"{command}.db"
            cli(database, command, source)
            store = Store(database, self.cfg)
            try:
                self.assertEqual(store.records(), [row])
            finally:
                store.close()
            exported = Path(self.tmp.name) / f"{command}.csv"
            cli(database, "export", exported)
            rows = list(
                csv.DictReader(io.StringIO(exported.read_bytes().decode(), newline=""))
            )
            self.assertEqual(rows, [row])
            cli(database, command, exported)

    async def test_backfill_preserves_exact_whitespace_ids(self):
        ids = ["demo ref", "demo  ref", " demo ref ", 'demo "ref"', "demo\\ref"]
        self.db.close()
        self.cfg = replace(self.cfg, watches=dict.fromkeys(ids, "Demo"))
        self.db = Store(self.path, self.cfg)
        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        async with app:
            for index, watch_id in enumerate(ids):
                # JSON string syntax provides an unambiguous exact-ID escape hatch.
                choice = watch_id if index < 2 else json.dumps(watch_id)
                await app.process_update(
                    command_update(
                        app.bot,
                        f"/set 2026-03-28 morning {choice}",
                        update_id=100 + index,
                    )
                )
                self.assertEqual(self.db.records()[0]["watch_id"], watch_id)

    async def test_missing_topic_requires_exact_persisted_binding(self):
        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        async with app:
            await app.bot_data["adapter"].tick(NOW)
            prompt = self.db.prompt("2026-03-29", "morning")

            async def dispatch(
                index, user=1, chat=2, message_id=None, topic=None, token=None
            ):
                message = {
                    "message_id": prompt["message_id"]
                    if message_id is None
                    else message_id,
                    "date": 0,
                    "chat": {"id": chat, "type": "supergroup"},
                }
                if topic is not None:
                    message["message_thread_id"] = topic
                update = Update.de_json(
                    {
                        "update_id": index,
                        "callback_query": {
                            "id": f"review-{index}",
                            "from": {
                                "id": user,
                                "is_bot": False,
                                "first_name": "Synthetic",
                            },
                            "chat_instance": "synthetic",
                            "data": (token or prompt["id"]) + ":0",
                            "message": message,
                        },
                    },
                    app.bot,
                )
                await app.process_update(update)

            for index, invalid in enumerate(
                [
                    {"user": 9},
                    {"chat": 9},
                    {"message_id": 999},
                    {"topic": 9},
                    {"token": "unknown"},
                ]
            ):
                await dispatch(index, **invalid)
                self.assertEqual(self.db.records(), [])
            for state, attempts, stored_id in [
                ("pending", 0, prompt["message_id"]),
                ("uncertain", 1, None),
                ("sent", 2, 999),
            ]:
                with self.db.connection:
                    self.db.connection.execute(
                        "UPDATE prompts SET state=?,attempts=?,message_id=? WHERE id=?",
                        (state, attempts, stored_id, prompt["id"]),
                    )
                await dispatch(19)
                self.assertEqual(self.db.records(), [])
            with self.db.connection:
                self.db.connection.execute(
                    "UPDATE prompts SET state='sent',attempts=1,message_id=? WHERE id=?",
                    (prompt["message_id"], prompt["id"]),
                )
            await dispatch(20)
            self.assertEqual(self.db.records()[0]["watch_id"], "demo.ref")
            before = self.db.export_csv()
            await dispatch(20)
            self.assertEqual(self.db.export_csv(), before)
            self.db.config = replace(self.cfg, topic=4)
            await dispatch(21)
            self.assertEqual(self.db.export_csv(), before)
