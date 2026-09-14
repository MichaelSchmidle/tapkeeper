"""Saved UI is a projection of durable history, never a replay receipt."""

from dataclasses import replace
from unittest.mock import AsyncMock

from telegram import Update

from tapkeeper.adapter import Adapter, application
from tapkeeper.core import Store
from test_commands import CommandRequest, command_update
from test_runtime import Fixture, NOW


class SavedPrompt(Fixture):
    async def asyncSetUp(self):
        self.fake.edit = AsyncMock()
        self.fake.alert = AsyncMock()
        await self.app.tick(NOW)
        self.prompt = self.db.prompt("2026-03-29", "morning")

    async def select(self, choice="0", key="save", **kwargs):
        return await self.app.callback(
            key,
            kwargs.get("user", 1),
            kwargs.get("chat", 2),
            kwargs.get("topic", 3),
            kwargs.get("data", self.prompt["id"] + ":" + choice),
            NOW,
            kwargs.get("message_id", self.prompt["message_id"]),
        )

    async def test_saved_change_replay_restart_projection(self):
        self.assertIn("choose a watch", self.fake.sent[0][0])
        await self.select()
        args = self.fake.edit.call_args.args
        self.assertEqual(
            args,
            (
                1,
                "Recorded 2026-03-29 morning: Demo",
                [("Change", self.prompt["id"] + ":change")],
            ),
        )
        before = self.db.export_csv()
        receipts = list(self.db.connection.execute("SELECT * FROM callbacks"))
        await self.select("change", "open")
        self.assertIn("Demo", self.fake.edit.call_args.args[1])
        self.assertIn("choose a watch", self.fake.edit.call_args.args[1])
        self.assertEqual(len(self.fake.edit.call_args.args[2]), 3)
        self.assertEqual(before, self.db.export_csv())
        self.assertEqual(
            receipts, list(self.db.connection.execute("SELECT * FROM callbacks"))
        )
        await self.select("none", "correct")
        before = self.db.export_csv()
        self.db.close()
        self.db = Store(self.path, self.cfg)
        self.app = Adapter(self.db, self.fake)
        await self.select()  # old receipt must not put Demo back on the message
        self.assertEqual(before, self.db.export_csv())
        self.assertIn("No watch", self.fake.edit.call_args.args[1])
        self.assertIn(
            "Demo",
            self.db.connection.execute(
                "SELECT response FROM callbacks WHERE id='callback:save'"
            ).fetchone()[0],
        )
        await self.select("change", "reopen")
        self.assertIn("No watch", self.fake.edit.call_args.args[1])
        self.assertEqual(before, self.db.export_csv())

    async def test_change_authorization_and_topic_binding(self):
        await self.select()
        before = self.db.export_csv()
        self.fake.edit.reset_mock()
        for invalid in (
            {"user": 9},
            {"chat": 9},
            {"topic": 9},
            {"topic": None, "message_id": 999},
            {"data": "unknown:change"},
        ):
            self.assertFalse(await self.select("change", "open", **invalid))
        self.fake.edit.assert_not_called()
        self.assertTrue(await self.select("change", "open", topic=None))
        self.assertEqual(before, self.db.export_csv())
        self.fake.edit.reset_mock()
        self.db.config = replace(self.cfg, topic=4)
        self.assertFalse(await self.select("change", "open", topic=None))
        self.fake.edit.assert_not_called()

    async def test_failed_write_keeps_choices_and_alerts(self):
        self.db.connection.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        self.assertFalse(await self.select())
        self.fake.edit.assert_not_called()
        self.fake.alert.assert_awaited_once()
        self.assertEqual(self.db.records(), [])
        self.assertEqual(self.fake.confirmed, [])

    async def test_edit_failure_is_not_write_failure_and_replay_repairs(self):
        async def fail(*args):
            other = Store(self.path, self.cfg)
            try:
                self.assertEqual(other.records()[0]["watch_id"], "demo.ref")
            finally:
                other.close()
            raise TimeoutError("synthetic")

        self.fake.edit.side_effect = fail
        await self.select()
        before = self.db.export_csv()
        self.fake.alert.assert_not_called()
        self.assertTrue(self.fake.confirmed[0].startswith("Recorded"))
        self.fake.edit.side_effect = None
        await self.select()
        self.assertEqual(before, self.db.export_csv())
        self.assertIn("Demo", self.fake.edit.call_args.args[1])

    async def test_same_snapshot_and_send_after_offline_backfill(self):
        await self.select()
        evening = self.db.prompt("2026-03-29", "evening")
        await self.app.callback(
            "same", 1, 2, 3, evening["id"] + ":same", NOW, evening["message_id"]
        )
        await self.select("1", "correct")
        await self.app.callback(
            "same", 1, 2, 3, evening["id"] + ":same", NOW, evening["message_id"]
        )
        self.assertIn("evening: Demo", self.fake.edit.call_args.args[1])
        self.db.backfill("2026-03-30", "morning", "none", NOW.replace(day=30))
        await self.app.tick(NOW.replace(day=30, hour=7))
        self.assertIn("No watch", self.fake.sent[-1][0])
        self.assertEqual(self.fake.sent[-1][1][0][0], "Change")

    async def test_set_replay_and_postcommit_projection_failure(self):
        from unittest.mock import patch
        import sqlite3

        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        async with app:
            first = command_update(
                app.bot, "/set 2026-03-29 morning demo.ref", update_id=30
            )
            await app.process_update(first)
            await app.process_update(
                command_update(app.bot, "/set 2026-03-29 morning none", update_id=31)
            )
            before = self.db.export_csv()
            await app.process_update(first)
            self.assertEqual(before, self.db.export_csv())
            edits = [
                params for name, params in request.calls if name == "editMessageText"
            ]
            self.assertIn("No watch", edits[-1]["text"])
            replies = [
                params for name, params in request.calls if name == "sendMessage"
            ]
            self.assertIn("No watch", replies[-1]["text"])
            request.calls.clear()
            with patch.object(
                self.db, "prompt", side_effect=sqlite3.OperationalError("synthetic")
            ):
                await app.process_update(
                    command_update(
                        app.bot, "/set 2026-03-29 morning demo.ref", update_id=32
                    )
                )
            self.assertEqual(self.db.records()[0]["watch_id"], "demo.ref")
            self.assertFalse(
                any("failed" in params.get("text", "") for _, params in request.calls)
            )

    async def test_transport_edit_noop_and_network_failure(self):
        from tapkeeper.adapter import TelegramTransport
        from telegram.error import BadRequest, TimedOut

        bot = AsyncMock()
        transport = TelegramTransport(bot, self.cfg)
        bot.edit_message_text.side_effect = BadRequest("Message is not modified")
        await transport.edit(1, "saved", [("Change", "token:change")])
        bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
        with self.assertRaises(BadRequest):
            await transport.edit(1, "saved", [])
        bot.edit_message_text.side_effect = TimedOut()
        with self.assertRaises(TimedOut):
            await transport.edit(1, "saved", [])
        bot.answer_callback_query.side_effect = TimedOut()
        self.app = Adapter(self.db, transport)
        await self.select()
        self.assertEqual(self.db.records()[0]["watch_id"], "demo.ref")
        self.assertFalse(
            any(
                call.kwargs.get("show_alert")
                for call in bot.answer_callback_query.call_args_list
            )
        )

    async def test_dispatch_change_alert_and_set_reconciliation(self):
        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        async with app:

            async def dispatch(choice, key):
                await app.process_update(
                    Update.de_json(
                        {
                            "update_id": 100,
                            "callback_query": {
                                "id": key,
                                "from": {
                                    "id": 1,
                                    "is_bot": False,
                                    "first_name": "Synthetic",
                                },
                                "chat_instance": "synthetic",
                                "data": self.prompt["id"] + ":" + choice,
                                "message": {
                                    "message_id": 1,
                                    "date": 1,
                                    "chat": {"id": 2, "type": "supergroup"},
                                },
                            },
                        },
                        app.bot,
                    )
                )

            await dispatch("0", "save")
            await dispatch("change", "open")
            edits = [
                params for name, params in request.calls if name == "editMessageText"
            ]
            self.assertEqual(len(edits), 2)
            self.assertEqual(
                edits[0]["reply_markup"]["inline_keyboard"][0][0]["text"], "Change"
            )
            self.assertIn("Demo", edits[1]["text"])
            await app.process_update(
                command_update(app.bot, "/set 2026-03-29 morning none")
            )
            edits = [
                params for name, params in request.calls if name == "editMessageText"
            ]
            self.assertIn("No watch", edits[-1]["text"])
            self.db.connection.execute(
                "CREATE TRIGGER fail BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'synthetic'); END"
            )
            await dispatch("1", "fail")
            alerts = [
                params
                for name, params in request.calls
                if name == "answerCallbackQuery"
            ]
            self.assertTrue(alerts[-1]["show_alert"])
            self.assertNotIn("Recorded", alerts[-1]["text"])
