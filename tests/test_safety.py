"""Redacted error feedback and seeded authorization through real PTB dispatch."""

from dataclasses import replace
from unittest.mock import patch
import sqlite3

from telegram import Update

from tapkeeper.adapter import application
from tapkeeper.core import Store
from test_commands import CommandRequest, command_update
from test_runtime import Fixture, NOW


STORAGE_ERROR = "Not recorded. Please retry. If this persists, check storage."
SELECTION_ERROR = "Cannot use this selection. Use an authorized check-in or /set."
SAME_ERROR = '"Same as morning" is unavailable. Please select a watch directly.'


def callback_update(
    bot,
    prompt,
    choice="0",
    key="tap",
    user=1,
    chat=1,
    topic=None,
    message_id=None,
    chat_type="private",
):
    message = {
        "message_id": prompt["message_id"] if message_id is None else message_id,
        "date": 1,
        "chat": {"id": chat, "type": chat_type},
    }
    if topic is not None:
        message["message_thread_id"] = topic
    return Update.de_json(
        {
            "update_id": 50,
            "callback_query": {
                "id": key,
                "from": {"id": user, "is_bot": False, "first_name": "Synthetic"},
                "chat_instance": "synthetic",
                "data": prompt["id"] + ":" + choice,
                "message": message,
            },
        },
        bot,
    )


class Safety(Fixture):
    def snapshot(self):
        return {
            table: [
                tuple(row)
                for row in self.db.connection.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            for table in ("records", "callbacks", "prompts")
        }

    async def asyncSetUp(self):
        await self.app.tick(NOW)
        self.prompt = self.db.prompt("2026-03-29", "morning")
        self.request = CommandRequest()
        self.telegram = application(self.db, "100:synthetic", self.request)
        self.errors = []

        async def capture(update, context):
            self.errors.append(context.error)

        self.telegram.add_error_handler(capture)
        await self.telegram.initialize()
        self.request.calls.clear()

    async def asyncTearDown(self):
        await self.telegram.shutdown()
        self.assertEqual(self.errors, [])

    async def dispatch(self, **kwargs):
        await self.telegram.process_update(
            callback_update(self.telegram.bot, self.prompt, **kwargs)
        )

    def assert_alert_only(self, text):
        self.assertEqual(len(self.request.calls), 1)
        name, params = self.request.calls[0]
        self.assertEqual(name, "answerCallbackQuery")
        self.assertIs(params["show_alert"], True)
        self.assertEqual(params["text"], text)
        self.assertEqual(self.request.uploads, [])

    async def test_error_copy_distinguishes_storage_domain_and_invalid_selection(self):
        before = self.snapshot()
        with patch.object(
            self.db,
            "select",
            side_effect=sqlite3.OperationalError("private-path-and-secret"),
        ):
            await self.dispatch()
        self.assert_alert_only(STORAGE_ERROR)
        self.assertEqual(self.snapshot(), before)
        self.request.calls.clear()
        await self.dispatch(choice="invalid")
        self.assert_alert_only(SELECTION_ERROR)
        self.assertEqual(self.snapshot(), before)
        self.prompt = self.db.prompt("2026-03-29", "evening")
        for morning in (None, "none"):
            if morning:
                self.db.backfill("2026-03-29", "morning", morning, NOW)
            before = self.snapshot()
            self.request.calls.clear()
            await self.dispatch(choice="same")
            self.assert_alert_only(SAME_ERROR)
            self.assertEqual(self.snapshot(), before)

    async def test_storage_failure_after_same_resolution_is_not_domain_advice(self):
        self.db.backfill("2026-03-29", "morning", "demo.ref", NOW)
        self.prompt = self.db.prompt("2026-03-29", "evening")
        self.db.connection.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON records "
            "BEGIN SELECT RAISE(ABORT, 'private-storage-detail'); END"
        )
        before = self.snapshot()
        await self.dispatch(choice="same")
        self.assert_alert_only(STORAGE_ERROR)
        self.assertEqual(self.snapshot(), before)

    async def test_seeded_history_and_replay_are_private_after_restart(self):
        await self.dispatch(key="saved")
        await self.telegram.process_update(
            command_update(
                self.telegram.bot, "/set 2026-03-28 morning demo.ref", update_id=10
            )
        )
        self.db.close()
        self.db = Store(self.path, self.cfg)
        # Recreate the whole dispatcher too, not just the connection.
        await self.telegram.shutdown()
        self.telegram = application(self.db, "100:synthetic", self.request)

        async def capture(update, context):
            self.errors.append(context.error)

        self.telegram.add_error_handler(capture)
        await self.telegram.initialize()
        before = self.snapshot()
        for identity in (
            {"user": 9},
            {"chat": 9},
            {"topic": 9},
            {"topic": None, "message_id": 999},
        ):
            for choice, key in (("1", "fresh"), ("0", "saved"), ("change", "open")):
                with self.subTest(identity=identity, choice=choice):
                    self.request.calls.clear()
                    self.request.uploads.clear()
                    await self.dispatch(choice=choice, key=key, **identity)
                    self.assert_alert_only(SELECTION_ERROR)
                    self.assertEqual(self.snapshot(), before)
        for identity in (
            {"user": 9},
            {"chat": 9},
            {"topic": 9},
            {"chat_type": "supergroup"},
            {"user": None},
        ):
            for text in ("/export", "/set 2026-03-28 morning none", "/help"):
                for update_id in (10, 11):
                    with self.subTest(
                        identity=identity, text=text, update_id=update_id
                    ):
                        self.request.calls.clear()
                        await self.telegram.process_update(
                            command_update(
                                self.telegram.bot, text, update_id=update_id, **identity
                            )
                        )
                        self.assertEqual(self.request.calls, [])
                        self.assertEqual(self.request.uploads, [])
                        self.assertEqual(self.snapshot(), before)
        # Private callbacks on the bound message still project saved history.
        await self.dispatch(choice="change", topic=None)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(
            any(name == "editMessageText" for name, _ in self.request.calls)
        )
        await self.telegram.process_update(command_update(self.telegram.bot, "/export"))
        self.assertEqual(len(self.request.uploads), 1)
        self.assertEqual(self.request.uploads[0][1].decode(), self.db.export_csv())

    async def test_reconfigured_identity_cannot_reopen_or_replay_old_prompt(self):
        await self.dispatch(key="saved")
        for field in ("user", "chat", "topic"):
            with self.db.connection:
                self.db.connection.execute(f"UPDATE prompts SET {field}=9")
            before = self.snapshot()
            for identity in ({}, {field: 9}):
                for choice in ("0", "1", "change", "same"):
                    with self.subTest(field=field, identity=identity, choice=choice):
                        self.request.calls.clear()
                        await self.dispatch(choice=choice, key="saved", **identity)
                        self.assert_alert_only(SELECTION_ERROR)
                        self.assertEqual(self.snapshot(), before)
            with self.db.connection:
                self.db.connection.execute(
                    f"UPDATE prompts SET {field}=?", (None if field == "topic" else 1,)
                )
        self.db.config = replace(self.cfg, user=9, chat=9)
        before = self.snapshot()
        for identity in ({}, {"user": 9, "chat": 9}):
            await self.dispatch(choice="change", key="saved", **identity)
            self.assertEqual(self.snapshot(), before)

    async def test_nonprivate_callbacks_cannot_replay_reopen_or_disclose(self):
        await self.dispatch(key="saved")
        before = self.snapshot()
        for chat_type in ("group", "supergroup", "channel"):
            for choice, key in (("0", "saved"), ("1", "fresh"), ("change", "open")):
                self.request.calls.clear()
                await self.dispatch(choice=choice, key=key, chat_type=chat_type)
                self.assertEqual(self.request.calls, [])
                self.assertEqual(self.request.uploads, [])
                self.assertEqual(self.snapshot(), before)
