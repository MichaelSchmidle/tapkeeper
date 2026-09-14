"""Transport boundary. Confirmation is deliberately outside the transaction."""

import asyncio
import json
import sqlite3
import sys

from datetime import datetime, timezone

from .core import SLOTS, due_time, local_date


def diagnostic(code):
    print(f"tapkeeper: {code}; consult status and operations runbook", file=sys.stderr)


class Adapter:
    def __init__(self, store, transport):
        self.store = store
        self.transport = transport

    async def tick(self, now):
        config = self.store.config
        day = local_date(now, config.timezone)
        for slot in SLOTS:
            if now < due_time(day, getattr(config, slot), config.timezone):
                continue
            prompt = self.store.ensure_prompt(day, slot)
            if (prompt["user"], prompt["chat"], prompt["topic"]) != (
                config.user,
                config.chat,
                config.topic,
            ):
                diagnostic("destination-mismatch: prompt suppressed")
                continue
            if not self.store.claim(prompt["id"], now):
                continue
            choices = json.loads(prompt["choices"])
            buttons = [
                (label, f"{prompt['id']}:{index}")
                for index, label in enumerate(choices.values())
            ]
            buttons.append(("No watch", f"{prompt['id']}:none"))
            if slot == "evening":
                buttons.append(("Same as morning", f"{prompt['id']}:same"))
            try:
                message_id = await self.transport.send(
                    f"{day} {slot}: choose a watch", buttons
                )
                self.store.delivered(prompt["id"], message_id)
            except Exception:
                # Claim remains uncertain, including cancellation/process death.
                diagnostic(
                    "prompt-send-uncertain"
                    if prompt["attempts"] < 2
                    else "prompt-attempts-exhausted"
                )
                continue

    async def callback(
        self, callback_id, user, chat, topic, data, now, message_id=None
    ):
        try:
            result = self.store.select(
                callback_id, user, chat, topic, data, now, message_id
            )
        except ValueError:
            return False
        except sqlite3.Error:
            diagnostic("callback-storage-failure: no success confirmed")
            return False
        try:
            await self.transport.confirm(callback_id, result)
        except Exception:
            return False
        return True


class TelegramTransport:
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config

    async def send(self, text, buttons):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        message = await self.bot.send_message(
            chat_id=self.config.chat,
            message_thread_id=self.config.topic,
            text=text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(label, callback_data=data)]
                    for label, data in buttons
                ]
            ),
        )
        return message.message_id

    async def confirm(self, callback_id, text):
        await self.bot.answer_callback_query(
            callback_query_id=callback_id, text=text[:200]
        )


def application(store, token, request=None):
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler

    builder = Application.builder().token(token)
    if request is not None:
        builder = builder.request(request)
    app = builder.build()
    adapter = Adapter(store, TelegramTransport(app.bot, store.config))
    app.bot_data["adapter"] = adapter

    async def callback(update, context):
        query = update.callback_query
        message = query.message
        if message is None:
            return
        ok = await adapter.callback(
            query.id,
            query.from_user.id,
            message.chat_id,
            getattr(message, "message_thread_id", None),
            query.data or "",
            datetime.now(timezone.utc),
            message.message_id,
        )
        if not ok:
            try:
                await query.answer(
                    "Not recorded or confirmation unavailable. Retry; if Same as morning "
                    "is unavailable, select a watch directly."
                )
            except Exception:
                pass

    async def command(update, context):
        message = update.effective_message
        if (
            message is None
            or update.effective_user is None
            or not store.authorized(
                update.effective_user.id, message.chat_id, message.message_thread_id
            )
        ):
            return
        try:
            parts = message.text.split(maxsplit=3)
            name = parts[0].split("@")[0]
            if name == "/export":
                import io

                await message.reply_document(
                    document=io.BytesIO(store.export_csv().encode("utf-8")),
                    filename="tapkeeper-v1.csv",
                )
            elif name == "/set" and len(parts) == 4:
                choice = parts[3]
                if choice.startswith('"'):
                    choice = json.loads(choice)
                    if not isinstance(choice, str):
                        raise ValueError("Watch ID must be a string")
                result = store.backfill(
                    parts[1],
                    parts[2],
                    choice,
                    datetime.now(timezone.utc),
                    update_id=update.update_id,
                )
                await message.reply_text(result)
            else:
                await message.reply_text(
                    "Use /set YYYY-MM-DD morning|evening WATCH_ID|none|same or /export"
                )
        except Exception:
            await message.reply_text(
                "Operation failed; no success confirmed. Check storage and retry."
            )

    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(CommandHandler(["set", "export", "help"], command))
    return app


def attach_scheduler(app):
    task = None

    async def start(application):
        nonlocal task

        async def scheduler():
            while True:
                try:
                    await application.bot_data["adapter"].tick(
                        datetime.now(timezone.utc)
                    )
                except Exception:
                    diagnostic("scheduler-storage-or-clock-failure")
                await asyncio.sleep(15)

        # Not Application.create_task: PTB stop waits for those before post_stop.
        task = asyncio.create_task(scheduler())

    async def stop(application):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.post_init = start
    app.post_stop = stop
    app.post_shutdown = stop


def run(store, token):
    app = application(store, token)
    attach_scheduler(app)
    app.run_polling(
        allowed_updates=["message", "callback_query"], drop_pending_updates=False
    )
