"""Exercise owner commands through the real PTB dispatcher without network I/O."""

import csv
import io
import json

from telegram import Update

from tapkeeper.adapter import application
from test_edges import Request
from test_runtime import Fixture


class CommandRequest(Request):
    def __init__(self):
        super().__init__()
        self.uploads = []

    async def do_request(self, url, method, request_data=None, **kwargs):
        if url.rsplit("/", 1)[-1] != "sendDocument":
            return await super().do_request(url, method, request_data, **kwargs)
        self.calls.append(("sendDocument", request_data.parameters))
        self.uploads.extend(request_data.multipart_data.values())
        message = {
            "message_id": len(self.calls),
            "date": 1,
            "chat": {"id": 1, "type": "private"},
            "document": {
                "file_id": "synthetic",
                "file_unique_id": "synthetic-unique",
            },
        }
        return 200, json.dumps({"ok": True, "result": message}).encode()


def command_update(
    bot, text, user=1, chat=1, topic=None, update_id=10, chat_type="private"
):
    message = {
        "message_id": update_id,
        "date": 1,
        "chat": {"id": chat, "type": chat_type},
        "text": text,
        "entities": [
            {"type": "bot_command", "offset": 0, "length": len(text.split()[0])}
        ],
    }
    if user is not None:
        message["from"] = {"id": user, "is_bot": False, "first_name": "Synthetic"}
    if topic is not None:
        message["message_thread_id"] = topic
    return Update.de_json({"update_id": update_id, "message": message}, bot)


class TelegramCommands(Fixture):
    async def test_missing_morning_guides_owner_to_direct_selection(self):
        from test_runtime import NOW

        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        async with app:
            await app.bot_data["adapter"].tick(NOW)
            prompt = self.db.prompt("2026-03-29", "evening")
            update = Update.de_json(
                {
                    "update_id": 20,
                    "callback_query": {
                        "id": "missing-morning",
                        "from": {"id": 1, "is_bot": False, "first_name": "Owner"},
                        "chat_instance": "synthetic",
                        "data": prompt["id"] + ":same",
                        "message": {
                            "message_id": prompt["message_id"],
                            "date": 1,
                            "chat": {"id": 1, "type": "private"},
                        },
                    },
                },
                app.bot,
            )
            await app.process_update(update)
        self.assertEqual(self.db.records(), [])
        replies = [
            params["text"]
            for name, params in request.calls
            if name == "answerCallbackQuery"
        ]
        self.assertEqual(len(replies), 1)
        self.assertIn("select a watch directly", replies[0])

    async def test_backfill_correction_no_watch_and_csv_document(self):
        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        errors = []

        async def collect_error(update, context):
            errors.append(context.error)

        app.add_error_handler(collect_error)
        async with app:
            await app.process_update(
                command_update(app.bot, "/set 2026-03-28 morning demo.ref")
            )
            self.assertEqual(self.db.records()[0]["watch_id"], "demo.ref")
            await app.process_update(
                command_update(app.bot, "/set 2026-03-28 morning none", update_id=11)
            )
            self.assertEqual(len(self.db.records()), 1)
            self.assertEqual(self.db.records()[0]["watch_id"], "")
            await app.process_update(command_update(app.bot, "/export", update_id=12))
        self.assertEqual(errors, [])
        self.assertEqual(len(request.uploads), 1)
        filename, payload, content_type = request.uploads[0]
        self.assertEqual(filename, "tapkeeper-v1.csv")
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
        self.assertEqual(rows, self.db.records())
        self.assertTrue(
            any(
                name == "sendMessage" and params["text"].startswith("Recorded")
                for name, params in request.calls
            )
        )

    async def test_wrong_identity_neither_writes_nor_exports(self):
        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        errors = []

        async def collect_error(update, context):
            errors.append(context.error)

        app.add_error_handler(collect_error)
        async with app:
            for identity in (
                {"user": 9},
                {"chat": 9},
                {"topic": 9},
                {"chat_type": "supergroup"},
                {"user": None},
            ):
                await app.process_update(
                    command_update(
                        app.bot, "/set 2026-03-28 morning demo.ref", **identity
                    )
                )
                await app.process_update(command_update(app.bot, "/export", **identity))
        self.assertEqual(self.db.records(), [])
        self.assertEqual(request.uploads, [])
        self.assertEqual([name for name, _ in request.calls], ["getMe"])
        self.assertEqual(errors, [])

    async def test_storage_failure_never_sends_recorded_confirmation(self):
        request = CommandRequest()
        app = application(self.db, "100:synthetic", request)
        self.db.connection.execute(
            "CREATE TRIGGER fail_command BEFORE INSERT ON records "
            "BEGIN SELECT RAISE(ABORT, 'synthetic'); END"
        )
        async with app:
            await app.process_update(
                command_update(app.bot, "/set 2026-03-28 morning demo.ref")
            )
        self.assertEqual(self.db.records(), [])
        replies = [
            params["text"] for name, params in request.calls if name == "sendMessage"
        ]
        self.assertEqual(len(replies), 1)
        self.assertFalse(replies[0].startswith("Recorded"))
        self.assertIn("failed", replies[0])
