from dataclasses import replace
import asyncio
import csv
import io
from pathlib import Path
from unittest.mock import patch

from telegram import Update

from tapkeeper.adapter import application, attach_scheduler
from tapkeeper.core import Config, Store
from test_edges import Request
from test_runtime import Fixture, NOW


class Additional(Fixture):
    async def test_first_uncertain_visible_prompt_remains_usable_after_retry(self):
        from datetime import timedelta

        self.fake.fail_send = True
        await self.app.tick(NOW)
        first_data = self.fake.sent[0][1][0][1]
        self.fake.fail_send = False
        await self.app.tick(NOW + timedelta(minutes=2))
        self.assertTrue(
            await self.app.callback(
                "first-visible", 1, 1, None, first_data, NOW, message_id=1
            )
        )
        self.assertEqual(len(self.db.records()), 1)

    async def test_command_replay_after_later_correction(self):
        request = Request()
        app = application(self.db, "100:synthetic", request)
        async with app:

            def command(update_id, watch):
                text = "/set 2026-03-28 morning " + watch
                return Update.de_json(
                    {
                        "update_id": update_id,
                        "message": {
                            "message_id": update_id,
                            "date": 1,
                            "from": {
                                "id": 1,
                                "is_bot": False,
                                "first_name": "Synthetic",
                            },
                            "chat": {"id": 1, "type": "private"},
                            "text": text,
                            "entities": [
                                {"type": "bot_command", "offset": 0, "length": 4}
                            ],
                        },
                    },
                    app.bot,
                )

            await app.process_update(command(10, "demo.ref"))
            await app.process_update(command(11, "other.ref"))
            before = self.db.export_csv()
            await app.process_update(command(10, "demo.ref"))
            self.assertEqual(self.db.export_csv(), before)

    async def test_scheduler_stop_cancels_inflight_send(self):
        app = application(self.db, "100:synthetic", Request())
        attach_scheduler(app)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hanging_tick(now):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        app.bot_data["adapter"].tick = hanging_tick
        async with app:
            await app.start()
            await app.post_init(app)
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.wait_for(app.stop(), 2)
            await asyncio.wait_for(app.post_stop(app), 2)
            self.assertTrue(cancelled.is_set())

    async def test_commands_use_authorized_handler_and_export(self):
        request = Request()
        app = application(self.db, "100:synthetic", request)
        async with app:

            def command(text, user=1):
                return Update.de_json(
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 4,
                            "date": 1,
                            "from": {
                                "id": user,
                                "is_bot": False,
                                "first_name": "Synthetic",
                            },
                            "chat": {"id": 1, "type": "private"},
                            "text": text,
                            "entities": [
                                {
                                    "type": "bot_command",
                                    "offset": 0,
                                    "length": len(text.split()[0]),
                                }
                            ],
                        },
                    },
                    app.bot,
                )

            await app.process_update(command("/set 2026-03-28 morning demo.ref", 9))
            self.assertEqual(self.db.records(), [])
            await app.process_update(command("/set 2026-03-28 morning demo.ref"))
            self.assertEqual(len(self.db.records()), 1)
            await app.process_update(command("/export"))
            self.assertTrue(
                any(name == "sendDocument" for name, params in request.calls)
            )

    async def test_backup_restores_prompts_and_callback_identity(self):
        await self.app.tick(NOW)
        prompt = self.db.prompt("2026-03-29", "morning")["id"]
        await self.tap(prompt)
        await self.tap(prompt, "other.ref", "correction")
        original = self.db.export_csv()
        backup = Path(self.tmp.name) / "all-state.db"
        self.db.backup(backup)
        restored = Path(self.tmp.name) / "restored-all.db"
        Store.restore(backup, restored)
        restored_db = Store(restored, self.cfg)
        try:
            restored_db.select("a", 1, 1, None, prompt + ":0", NOW)
            self.assertEqual(restored_db.export_csv(), original)
            self.assertEqual(
                restored_db.prompt("2026-03-29", "morning")["state"], "sent"
            )
        finally:
            restored_db.close()
        with self.assertRaises(FileExistsError):
            Store.restore(backup, restored)

    async def test_uncertain_send_diagnostics_are_redacted(self):
        self.fake.fail_send = True
        with patch("sys.stderr", new_callable=io.StringIO) as output:
            await self.app.tick(NOW)
        self.assertIn("prompt-send-uncertain", output.getvalue())
        self.assertNotIn("demo.ref", output.getvalue())
        self.assertNotIn("Demo", output.getvalue())

    def test_csv_validation_table_and_unicode_losslessness(self):
        self.db.backfill("2026-03-28", "morning", "demo.ref", NOW)
        original = self.db.export_csv()
        for field, value in [
            ("date", "2026-02-30"),
            ("slot", "night"),
            ("watch_id", "unknown"),
            ("watch", ""),
            ("recorded_at", "2026-03-28T12:00:00"),
            ("recorded_at", "2027-01-01T00:00:00+00:00"),
            ("source", ""),
        ]:
            rows = list(csv.DictReader(io.StringIO(original)))
            rows[0][field] = value
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.db.import_csv(output.getvalue(), NOW)
            self.assertEqual(self.db.export_csv(), original)
        rows = list(csv.DictReader(io.StringIO(original)))
        rows[0]["date"] = "2026-03-27"
        rows[0]["watch"] = '=Synthetic, "é"\nHistorical label'
        rows[0]["source"] = "legacy, synthetic"
        rows[0]["recorded_at"] = "2026-03-28T21:00:00+02:00"
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        self.db.import_csv(output.getvalue(), NOW)
        self.assertEqual(self.db.records()[0], rows[0])
        self.assertEqual(
            list(csv.DictReader(io.StringIO(self.db.export_csv())))[0], rows[0]
        )

    def test_abrupt_process_exit_after_claim_preserves_uncertainty(self):
        import json
        import subprocess
        import sys

        code = """
import json, os, sys
from datetime import datetime
from tapkeeper.core import Config, Store
store = Store(sys.argv[1], Config(**json.loads(sys.argv[2])))
prompt = store.ensure_prompt('2026-03-29', 'morning')
store.claim(prompt['id'], datetime.fromisoformat(sys.argv[3]))
os._exit(23)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(self.path),
                json.dumps(self.cfg.__dict__),
                NOW.isoformat(),
            ],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 23)
        prompt = self.db.prompt("2026-03-29", "morning")
        self.assertEqual(prompt["attempts"], 1)
        self.assertEqual(prompt["state"], "uncertain")
        self.assertFalse(self.db.claim(prompt["id"], NOW))

    def test_reject_duplicate_config_keys(self):
        path = Path(self.tmp.name) / "duplicate.json"
        path.write_text('{"user":1,"user":2}', encoding="utf-8")
        with self.assertRaises(ValueError):
            Config.load(path)

    def test_config_invalid_inputs(self):
        for values in (
            {"morning": "24:00"},
            {"chat": -1},
            {"user": True},
            {"watches": {}},
            {"watches": {"   ": "Invalid"}},
            {"chat": 0},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(self.cfg, **values)
        self.assertIsInstance(self.cfg, Config)
