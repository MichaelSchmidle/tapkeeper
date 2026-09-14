"""Transactional single-owner state and version-1 CSV interchange."""

import csv
import io
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
SLOTS = ("morning", "evening")
FIELDS = ("date", "slot", "watch_id", "watch", "recorded_at", "source")


def local_date(now, zone):
    if now.tzinfo is None:
        raise ValueError("Aware time required")
    return now.astimezone(ZoneInfo(zone)).date().isoformat()


def valid_date(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Invalid date")
    date.fromisoformat(value)


def due_time(day, clock, zone):
    """Gap advances by minutes to first valid wall minute; overlap uses fold=0."""
    wall = datetime.fromisoformat(f"{day}T{clock}")
    tz = ZoneInfo(zone)
    for _ in range(1441):
        candidate = wall.replace(tzinfo=tz, fold=0).astimezone(UTC)
        if candidate.astimezone(tz).replace(tzinfo=None) == wall:
            return candidate
        wall += timedelta(minutes=1)
    raise ValueError("No valid local time")


@dataclass(frozen=True)
class Config:
    user: int
    chat: int
    topic: int | None
    timezone: str
    morning: str
    evening: str
    watches: dict

    def __post_init__(self):
        if (
            type(self.user) is not int
            or self.user <= 0
            or type(self.chat) is not int
            or self.chat == 0
        ):
            raise ValueError("Invalid destination")
        if self.topic is not None and (type(self.topic) is not int or self.topic <= 0):
            raise ValueError("Invalid topic")
        ZoneInfo(self.timezone)
        for clock in (self.morning, self.evening):
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
                raise ValueError("Invalid clock")
        if not isinstance(self.watches, dict) or not self.watches:
            raise ValueError("Empty catalogue")
        for key, value in self.watches.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or key in ("none", "same")
                or any(ord(c) < 32 for c in key)
            ):
                raise ValueError("Invalid watch ID")
            if not isinstance(value, str) or not value.strip() or len(value) > 100:
                raise ValueError("Invalid label")

    @classmethod
    def load(cls, path):
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate configuration key")
                result[key] = value
            return result

        return cls(
            **json.loads(
                Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_pairs
            )
        )


class Store:
    def __init__(self, path, config):
        self.path = Path(path)
        self.config = config
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.connection.close()
            raise ValueError("Unsupported schema version")
        if version == 0:
            if self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchone():
                self.connection.close()
                raise ValueError("Unversioned nonempty database")
            self.connection.executescript("""
                BEGIN;
                CREATE TABLE watches(id TEXT PRIMARY KEY, label TEXT NOT NULL, active INTEGER NOT NULL);
                CREATE TABLE records(date TEXT NOT NULL, slot TEXT NOT NULL CHECK(slot IN ('morning','evening')),
                    watch_id TEXT NOT NULL, watch TEXT NOT NULL, recorded_at TEXT NOT NULL, source TEXT NOT NULL,
                    PRIMARY KEY(date,slot));
                CREATE TABLE prompts(id TEXT PRIMARY KEY, date TEXT NOT NULL, slot TEXT NOT NULL,
                    user INTEGER NOT NULL, chat INTEGER NOT NULL, topic INTEGER, choices TEXT NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts <= 3),
                    last_attempt TEXT, message_id INTEGER, UNIQUE(date,slot));
                CREATE TABLE callbacks(id TEXT PRIMARY KEY, response TEXT NOT NULL);
                PRAGMA user_version=1;
                COMMIT;
            """)
        with self.connection:
            self.connection.execute("UPDATE watches SET active=0")
            for key, label in config.watches.items():
                self.connection.execute(
                    "INSERT INTO watches VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET label=excluded.label,active=1",
                    (key, label),
                )

    def close(self):
        self.connection.close()

    def authorized(self, user, chat, topic):
        return (user, chat, topic) == (
            self.config.user,
            self.config.chat,
            self.config.topic,
        )

    def prompt(self, day, slot):
        row = self.connection.execute(
            "SELECT * FROM prompts WHERE date=? AND slot=?", (day, slot)
        ).fetchone()
        return dict(row) if row else None

    def ensure_prompt(self, day, slot):
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO prompts(id,date,slot,user,chat,topic,choices,state) VALUES(?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex[:24],
                    day,
                    slot,
                    self.config.user,
                    self.config.chat,
                    self.config.topic,
                    json.dumps(self.config.watches),
                    "pending",
                ),
            )
        return self.prompt(day, slot)

    def claim(self, prompt_id, now):
        """Commit uncertainty BEFORE network I/O, including crash immediately after claim."""
        with self.connection:
            result = self.connection.execute(
                """UPDATE prompts SET state='uncertain', attempts=attempts+1,last_attempt=?
                WHERE id=? AND state IN ('pending','uncertain') AND attempts<3
                AND (last_attempt IS NULL OR last_attempt<=?)""",
                (
                    now.astimezone(UTC).isoformat(),
                    prompt_id,
                    (now.astimezone(UTC) - timedelta(seconds=60)).isoformat(),
                ),
            )
        return result.rowcount == 1

    def delivered(self, prompt_id, message_id):
        with self.connection:
            self.connection.execute(
                "UPDATE prompts SET state='sent',message_id=? WHERE id=?",
                (message_id, prompt_id),
            )

    def _validate(self, day, slot, now):
        valid_date(day)
        if slot not in SLOTS or day > local_date(now, self.config.timezone):
            raise ValueError("Invalid date or slot")

    def _save(self, day, slot, choice, now, source):
        self._validate(day, slot, now)
        if choice == "same":
            row = self.connection.execute(
                "SELECT watch_id,watch FROM records WHERE date=? AND slot='morning'",
                (day,),
            ).fetchone()
            if slot != "evening" or row is None or not row["watch_id"]:
                raise ValueError("Select directly: no morning watch")
            key, label = row
        elif choice == "none":
            key, label = "", ""
        else:
            row = self.connection.execute(
                "SELECT id,label FROM watches WHERE id=?", (choice,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown watch")
            key, label = row
        self.connection.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(date,slot) DO UPDATE SET watch_id=excluded.watch_id,watch=excluded.watch,recorded_at=excluded.recorded_at,source=excluded.source",
            (day, slot, key, label, now.astimezone(UTC).isoformat(), source),
        )
        return f"Recorded {day} {slot}: {label or 'No watch'}"

    def callback_prompt(self, callback_id, user, chat, topic, data, message_id=None):
        """Read-only callback authorization, shared by selection and Change."""
        if (user, chat) != (self.config.user, self.config.chat):
            raise ValueError("Unauthorized")
        if (
            not isinstance(callback_id, str)
            or not callback_id
            or len(callback_id) > 200
        ):
            raise ValueError("Invalid callback")
        if not isinstance(data, str) or len(data.encode()) > 64:
            raise ValueError("Invalid callback data")
        parts = data.split(":")
        if len(parts) != 2:
            raise ValueError("Invalid selection")
        prompt_id, choice = parts
        p = self.connection.execute(
            "SELECT * FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()
        # Missing topic metadata is recoverable only from an exact durable
        # token/chat/message binding, never merely the current configuration.
        if (
            p is not None
            and topic is None
            and message_id is not None
            and p["chat"] == chat
            and p["message_id"] == message_id
            and p["state"] != "pending"
        ):
            topic = p["topic"]
        if not self.authorized(user, chat, topic):
            raise ValueError("Unauthorized")
        if p is None or (p["user"], p["chat"], p["topic"]) != (user, chat, topic):
            raise ValueError("Unknown prompt")
        if message_id is not None and (
            p["state"] == "pending"
            or (
                p["attempts"] == 1
                and p["message_id"] is not None
                and p["message_id"] != message_id
            )
        ):
            raise ValueError("Wrong prompt message")
        return dict(p), choice

    def record(self, day, slot):
        row = self.connection.execute(
            "SELECT * FROM records WHERE date=? AND slot=?", (day, slot)
        ).fetchone()
        return dict(row) if row else None

    def select(self, callback_id, user, chat, topic, data, now, message_id=None):
        # IMMEDIATE prevents competing read/modify/write transactions.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            p, choice = self.callback_prompt(
                callback_id, user, chat, topic, data, message_id
            )
            callback_id = f"callback:{callback_id}"
            choices = list(json.loads(p["choices"]))
            if choice not in ("same", "none"):
                if (
                    not choice.isascii()
                    or not choice.isdecimal()
                    or str(int(choice)) != choice
                    or int(choice) >= len(choices)
                ):
                    raise ValueError("Invalid selection")
                choice = choices[int(choice)]
            previous = self.connection.execute(
                "SELECT response FROM callbacks WHERE id=?", (callback_id,)
            ).fetchone()
            if previous:
                return previous[0]
            response = self._save(p["date"], p["slot"], choice, now, "telegram")
            self.connection.execute(
                "INSERT INTO callbacks VALUES(?,?)", (callback_id, response)
            )
            return response

    def backfill(self, day, slot, choice, now, update_id=None):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            receipt = None if update_id is None else f"update:{update_id}"
            if receipt is not None:
                previous = self.connection.execute(
                    "SELECT response FROM callbacks WHERE id=?", (receipt,)
                ).fetchone()
                if previous:
                    return previous[0]
            result = self._save(day, slot, choice, now, "backfill")
            if receipt is not None:
                self.connection.execute(
                    "INSERT INTO callbacks VALUES(?,?)", (receipt, result)
                )
            return result

    def records(self):
        return [
            dict(r)
            for r in self.connection.execute(
                "SELECT * FROM records ORDER BY date,slot DESC"
            )
        ]

    def export_csv(self):
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(self.records())
        return output.getvalue()

    def import_csv(self, text, now):
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("Invalid CSV header")
        rows = {}
        for row in reader:
            if set(row) != set(FIELDS) or any(v is None for v in row.values()):
                raise ValueError("Invalid CSV row")
            self._validate(row["date"], row["slot"], now)
            stamp = datetime.fromisoformat(row["recorded_at"])
            if stamp.tzinfo is None or stamp > now or not row["source"].strip():
                raise ValueError("Invalid timestamp or source")
            if not row["watch_id"]:
                if row["watch"]:
                    raise ValueError("Invalid no-watch")
            elif (
                not row["watch"].strip()
                or not self.connection.execute(
                    "SELECT 1 FROM watches WHERE id=?", (row["watch_id"],)
                ).fetchone()
            ):
                raise ValueError("Unknown ID or missing label")
            key = (row["date"], row["slot"])
            if key in rows and rows[key] != row:
                raise ValueError("Conflicting duplicate")
            rows[key] = row
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            for key, row in rows.items():
                old = self.connection.execute(
                    "SELECT * FROM records WHERE date=? AND slot=?", key
                ).fetchone()
                if old and dict(old) != row:
                    raise ValueError("Conflicting existing record")
                if not old:
                    self.connection.execute(
                        "INSERT INTO records VALUES(?,?,?,?,?,?)",
                        tuple(row[f] for f in FIELDS),
                    )

    def backup(self, destination):
        destination = Path(destination)
        with destination.open("xb"):
            pass
        try:
            target = sqlite3.connect(destination)
            try:
                self.connection.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity failure")
            finally:
                target.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def restore(source, destination):
        source = Path(source).resolve()
        db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            if (
                db.execute("PRAGMA user_version").fetchone()[0] != 1
                or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            ):
                raise ValueError("Invalid backup")
            holder = object.__new__(Store)
            holder.connection = db
            holder.backup(destination)
        finally:
            db.close()
