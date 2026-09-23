import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tapkeeper.core import Store, Config, due_time
from tapkeeper.adapter import Adapter

UTC = timezone.utc
NOW = datetime(2026, 3, 29, 19, tzinfo=UTC)


class Fake:
    def __init__(self):
        self.sent = []
        self.confirmed = []
        self.fail_send = False
        self.fail_confirm = False

    async def send(self, text, buttons):
        self.sent.append((text, buttons))
        if self.fail_send:
            raise TimeoutError()
        return len(self.sent)

    async def edit(self, message_id, text, buttons):
        pass

    async def alert(self, callback, text):
        pass

    async def confirm(self, callback, text):
        if self.fail_confirm:
            raise TimeoutError()
        self.confirmed.append(text)


class Fixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.db"
        self.cfg = Config(
            1,
            1,
            "Europe/Zurich",
            "10:00",
            "20:00",
            {"demo.ref": "Demo", "other.ref": "Other"},
        )
        self.db = Store(self.path, self.cfg)
        self.fake = Fake()
        self.app = Adapter(self.db, self.fake)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    async def tap(self, p, choice="demo.ref", key="a", identity=(1, 1, None)):
        return await self.app.callback(
            key,
            *identity,
            f"{p}:{ {'demo.ref': '0', 'other.ref': '1'}.get(choice, choice) }",
            NOW,
        )


class Runtime(Fixture):
    async def test_full_restart_correction_snapshot_export(self):
        await self.app.tick(NOW)
        self.assertEqual(len(self.fake.sent), 2)
        morning = self.db.prompt("2026-03-29", "morning")["id"]
        evening = self.db.prompt("2026-03-29", "evening")["id"]
        await self.tap(morning)
        await self.tap(evening, "same", "b")
        await self.tap(morning, "other.ref", "c")
        self.assertEqual(self.db.records()[1]["watch_id"], "demo.ref")
        before = self.db.export_csv()
        self.db.close()
        self.db = Store(self.path, self.cfg)
        self.app = Adapter(self.db, self.fake)
        await self.tap(morning, "demo.ref", "a")
        self.assertEqual(before, self.db.export_csv())
        await self.app.tick(NOW)
        self.assertEqual(len(self.fake.sent), 2)
        await self.tap(morning, "none", "d")
        self.assertEqual(self.db.records()[0]["watch_id"], "")

    async def test_authorization_invalid_atomic(self):
        await self.app.tick(NOW)
        p = self.db.prompt("2026-03-29", "morning")["id"]
        for ident in [(9, 1, None), (1, 9, None), (1, 1, 3)]:
            self.assertFalse(await self.tap(p, identity=ident))
        self.assertFalse(await self.tap(p, "unknown"))
        self.assertEqual(self.db.records(), [])
        with self.assertRaises(ValueError):
            self.db.backfill("2026-03-30", "morning", "demo.ref", NOW)
        with self.assertRaises(ValueError):
            self.db.backfill("2026-03-28", "bad", "demo.ref", NOW)
        self.assertFalse(
            await self.tap(self.db.prompt("2026-03-29", "evening")["id"], "same")
        )

    async def test_commit_fail_and_confirmation_fail(self):
        await self.app.tick(NOW)
        p = self.db.prompt("2026-03-29", "morning")["id"]
        self.db.connection.execute(
            "CREATE TRIGGER fail BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        self.assertFalse(await self.tap(p))
        self.assertEqual(self.db.records(), [])
        self.assertEqual(self.fake.confirmed, [])
        self.db.connection.execute("DROP TRIGGER fail")
        self.fake.fail_confirm = True
        self.assertFalse(await self.tap(p))
        saved = self.db.export_csv()
        self.assertEqual(len(self.db.records()), 1)
        self.fake.fail_confirm = False
        await self.tap(p)
        self.assertEqual(saved, self.db.export_csv())

    async def test_retry_uncertainty_restart_bound(self):
        self.fake.fail_send = True
        await self.app.tick(NOW)
        self.assertEqual(len(self.fake.sent), 2)
        self.db.close()
        self.db = Store(self.path, self.cfg)
        self.app = Adapter(self.db, self.fake)
        await self.app.tick(NOW)
        self.assertEqual(len(self.fake.sent), 2)
        for minute in (2, 4, 6):
            await self.app.tick(NOW.replace(minute=minute))
        self.assertEqual(len(self.fake.sent), 6)
        self.assertEqual(self.db.prompt("2026-03-29", "morning")["state"], "uncertain")

    async def test_backfill_roundtrip_legacy_backup(self):
        self.db.backfill("2026-03-28", "morning", "demo.ref", NOW)
        text = self.db.export_csv().replace("Demo", "Historical label")
        other = Store(Path(self.tmp.name) / "other.db", self.cfg)
        try:
            other.import_csv(text, NOW)
            other.import_csv(text, NOW)
            self.assertEqual(other.export_csv(), text)
            with self.assertRaises(ValueError):
                other.import_csv(text.replace("Historical label", "Conflict"), NOW)
            with self.assertRaises(ValueError):
                other.import_csv(
                    text
                    + text.splitlines()[1].replace("2026-03-28", "2026-03-30")
                    + "\n",
                    NOW,
                )
            self.assertEqual(other.export_csv(), text)
            backup = Path(self.tmp.name) / "backup.db"
            other.backup(backup)
            restored = Path(self.tmp.name) / "restored.db"
            Store.restore(backup, restored)
            r = Store(restored, self.cfg)
            self.assertEqual(r.export_csv(), text)
            r.close()
        finally:
            other.close()

    def test_dst(self):
        self.assertEqual(
            due_time("2026-03-29", "02:30", "Europe/Zurich"),
            datetime(2026, 3, 29, 1, tzinfo=UTC),
        )
        self.assertEqual(
            due_time("2026-10-25", "02:30", "Europe/Zurich"),
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        )


if __name__ == "__main__":
    unittest.main()
