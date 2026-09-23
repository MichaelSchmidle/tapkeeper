import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from telegram import Update
from telegram.request import BaseRequest

from tapkeeper.adapter import Adapter, application
from tapkeeper.core import Store
from test_runtime import NOW, Fixture


class Edges(Fixture):
    async def test_callback_insert_failure_rolls_back_record(self):
        await self.app.tick(NOW)
        prompt = self.db.prompt("2026-03-29", "morning")["id"]
        self.db.connection.execute(
            "CREATE TRIGGER fail_callback BEFORE INSERT ON callbacks BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        self.assertFalse(await self.tap(prompt))
        self.assertEqual(self.db.records(), [])
        self.assertEqual(self.fake.confirmed, [])
        self.assertEqual(
            self.db.connection.execute("SELECT count(*) FROM callbacks").fetchone()[0],
            0,
        )

    async def test_malformed_and_absent_callback(self):
        for data in (None, "", "x", "x:y:z", "unknown:0"):
            self.assertFalse(await self.app.callback("bad", 1, 1, None, data, NOW))
        self.assertEqual(self.db.records(), [])

    async def test_retired_prompt_long_exact_id(self):
        key = "Synthetic:" + "é" * 100
        self.db.close()
        config = replace(self.cfg, watches={key: "Original"})
        self.db = Store(self.path, config)
        self.app = Adapter(self.db, self.fake)
        await self.app.tick(NOW)
        data = self.fake.sent[0][1][0][1]
        self.assertLessEqual(len(data.encode()), 64)
        self.db.close()
        self.db = Store(self.path, self.cfg)
        self.app = Adapter(self.db, self.fake)
        self.assertTrue(
            await self.app.callback("old", 1, 1, None, data, NOW + timedelta(days=2))
        )
        self.assertEqual(self.db.records()[0]["watch_id"], key)
        self.assertEqual(self.db.records()[0]["date"], "2026-03-29")

    async def test_destination_change_does_not_send_old_catalogue(self):
        self.db.ensure_prompt("2026-03-29", "morning")
        self.db.close()
        self.db = Store(self.path, replace(self.cfg, user=99, chat=99))
        self.app = Adapter(self.db, self.fake)
        await self.app.tick(NOW)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(self.db.prompt("2026-03-29", "morning")["attempts"], 0)

    async def test_claim_crash_and_current_day_only(self):
        old = self.db.ensure_prompt("2026-03-28", "morning")
        prompt = self.db.ensure_prompt("2026-03-29", "morning")
        self.assertTrue(self.db.claim(prompt["id"], NOW))
        self.db.close()
        self.db = Store(self.path, self.cfg)
        self.app = Adapter(self.db, self.fake)
        await self.app.tick(NOW)
        self.assertEqual(self.db.prompt("2026-03-29", "morning")["state"], "uncertain")
        self.assertEqual(self.db.prompt(old["date"], old["slot"])["attempts"], 0)

    def test_import_late_conflict_rolls_back_all(self):
        self.db.backfill("2026-03-28", "morning", "demo.ref", NOW)
        original = self.db.export_csv()
        row = original.splitlines()[1]
        payload = (
            original.splitlines()[0]
            + "\n"
            + row.replace("2026-03-28", "2026-03-27")
            + "\n"
            + row.replace("Demo", "Conflict")
            + "\n"
        )
        with self.assertRaises(ValueError):
            self.db.import_csv(payload, NOW)
        self.assertEqual(original, self.db.export_csv())

    def test_cli_process_restart_and_backup(self):
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps({"watches": self.cfg.watches}))
        db = Path(self.tmp.name) / "cli.db"

        def cli(command, *arguments, database=db):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tapkeeper.cli",
                    "--config",
                    str(config),
                    "--db",
                    str(database),
                    command,
                    *map(str, arguments),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        cli("init")
        cli("set", "2026-03-28", "morning", "demo.ref")
        exported = Path(self.tmp.name) / "v1.csv"
        cli("export", exported)
        other = Path(self.tmp.name) / "import.db"
        cli("import-legacy", exported, database=other)
        cli("import-v1", exported, database=other)
        backup = Path(self.tmp.name) / "cli-backup.db"
        cli("backup", backup, database=other)
        restored = Path(self.tmp.name) / "cli-restored.db"
        cli("restore", backup, database=restored)
        final = Path(self.tmp.name) / "final.csv"
        cli("export", final, database=restored)
        self.assertEqual(exported.read_bytes(), final.read_bytes())


class Request(BaseRequest):
    def __init__(self):
        self.calls = []

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        parameters = request_data.parameters if request_data else {}
        self.calls.append((name, parameters))
        if name == "getMe":
            result = {
                "id": 100,
                "is_bot": True,
                "first_name": "Synthetic",
                "username": "synthetic_bot",
            }
        elif name == "sendMessage":
            result = {
                "message_id": len(self.calls),
                "date": 1,
                "chat": {"id": 1, "type": "private"},
                "text": parameters["text"],
            }
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


class TelegramLifecycle(Fixture):
    async def test_actual_application_update_dispatch(self):
        request = Request()
        app = application(self.db, "100:synthetic", request)
        async with app:
            await app.start()
            try:
                await app.bot_data["adapter"].tick(NOW)
                prompt = self.db.prompt("2026-03-29", "morning")

                def update(callback_id, message_id, user=1):
                    return Update.de_json(
                        {
                            "update_id": 1,
                            "callback_query": {
                                "id": callback_id,
                                "from": {
                                    "id": user,
                                    "is_bot": False,
                                    "first_name": "Owner",
                                },
                                "chat_instance": "synthetic",
                                "data": prompt["id"] + ":0",
                                "message": {
                                    "message_id": message_id,
                                    "date": 1,
                                    "chat": {"id": 1, "type": "private"},
                                },
                            },
                        },
                        app.bot,
                    )

                await app.process_update(update("wrong-message", 999))
                self.assertEqual(self.db.records(), [])
                await app.process_update(update("wrong-user", prompt["message_id"], 9))
                self.assertEqual(self.db.records(), [])
                await app.process_update(update("valid", prompt["message_id"]))
                self.assertEqual(len(self.db.records()), 1)
                self.assertTrue(
                    any(
                        name == "answerCallbackQuery"
                        and params.get("text", "").startswith("Recorded")
                        for name, params in request.calls
                    )
                )
            finally:
                await asyncio.wait_for(app.stop(), 2)
