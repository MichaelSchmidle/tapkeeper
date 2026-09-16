"""Transport boundary. Confirmation is deliberately outside the transaction."""

import asyncio
import json
import sqlite3
import sys

from datetime import datetime, timezone

from .core import SLOTS, SameAsMorningUnavailable, due_time, local_date


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
            try:
                text, buttons = self.presentation(prompt)
                message_id = await self.transport.send(text, buttons)
                self.store.delivered(prompt["id"], message_id)
            except Exception:
                # Claim remains uncertain, including cancellation/process death.
                diagnostic(
                    "prompt-send-uncertain"
                    if prompt["attempts"] < 2
                    else "prompt-attempts-exhausted"
                )
                continue

    def presentation(self, prompt, reopen=False):
        """Render current durable history, not the original replay receipt."""
        record = self.store.record(prompt["date"], prompt["slot"])
        title = f"{prompt['date']} {prompt['slot']}"
        text = f"Recorded {title}: {record['watch'] or 'No watch'}" if record else title
        if record and not reopen:
            return text, [("Change", f"{prompt['id']}:change")]
        choices = json.loads(prompt["choices"])
        buttons = [
            (label, f"{prompt['id']}:{index}")
            for index, label in enumerate(choices.values())
        ]
        buttons.append(("No watch", f"{prompt['id']}:none"))
        if prompt["slot"] == "evening":
            buttons.append(("Same as morning", f"{prompt['id']}:same"))
        return text + (
            "\nChange: choose a watch" if record else ": choose a watch"
        ), buttons

    async def refresh(self, prompt, message_id=None, reopen=False):
        """Best effort only: a display failure cannot invalidate a committed answer."""
        target = message_id if message_id is not None else prompt["message_id"]
        if target is None:
            return
        try:
            text, buttons = self.presentation(prompt, reopen)
            await self.transport.edit(target, text, buttons)
        except Exception:
            diagnostic("prompt-display-unavailable: history unchanged")

    async def callback(
        self, callback_id, user, chat, topic, data, now, message_id=None
    ):
        try:
            prompt, choice = self.store.callback_prompt(
                callback_id, user, chat, topic, data, message_id
            )
            if choice != "change":
                self.store.select(callback_id, user, chat, topic, data, now, message_id)
        except (ValueError, sqlite3.Error) as error:
            if isinstance(error, sqlite3.Error):
                diagnostic("callback-storage-failure: no success confirmed")
                text = "Not recorded. Please retry. If this persists, check storage."
            elif isinstance(error, SameAsMorningUnavailable):
                text = (
                    '"Same as morning" is unavailable. Please select a watch directly.'
                )
            else:
                # Do not disclose prompt existence, saved history or raw error details.
                text = "Cannot use this selection. Use an authorized check-in or /set."
            try:
                await self.transport.alert(callback_id, text)
            except Exception:
                pass
            return False
        # All network I/O below is after commit (or an authorized read-only Change).
        await self.refresh(prompt, message_id, reopen=choice == "change")
        try:
            text, _ = self.presentation(prompt)
            await self.transport.confirm(
                callback_id,
                "Choose a replacement; saved answer is unchanged."
                if choice == "change"
                else text,
            )
        except Exception:
            diagnostic("callback-confirmation-unavailable: history unchanged")
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

    async def edit(self, message_id, text, buttons):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.error import BadRequest

        try:
            await self.bot.edit_message_text(
                chat_id=self.config.chat,
                message_id=message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(label, callback_data=data)]
                        for label, data in buttons
                    ]
                ),
            )
        except BadRequest as error:
            # Retried projection is already correct; other API failures remain failures.
            if "message is not modified" not in str(error).lower():
                raise

    async def alert(self, callback_id, text):
        await self.bot.answer_callback_query(
            callback_query_id=callback_id, text=text[:200], show_alert=True
        )

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
        await adapter.callback(
            query.id,
            query.from_user.id,
            message.chat_id,
            getattr(message, "message_thread_id", None),
            query.data or "",
            datetime.now(timezone.utc),
            message.message_id,
        )

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
                store.backfill(
                    parts[1],
                    parts[2],
                    choice,
                    datetime.now(timezone.utc),
                    update_id=update.update_id,
                )
                try:
                    # Reconcile only the known message in the still-authorized destination.
                    prompt = store.prompt(parts[1], parts[2])
                    if prompt and store.authorized(
                        prompt["user"], prompt["chat"], prompt["topic"]
                    ):
                        await adapter.refresh(prompt)
                    record = store.record(parts[1], parts[2])
                    await message.reply_text(
                        f"Recorded {parts[1]} {parts[2]}: {record['watch'] or 'No watch'}"
                    )
                except Exception:
                    diagnostic("command-confirmation-unavailable: history unchanged")
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
