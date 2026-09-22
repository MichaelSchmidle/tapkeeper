"""Regression for competing connections, not just sequential callback replay."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from tapkeeper.core import Store
from test_runtime import Fixture, NOW


class Transactions(Fixture):
    def test_concurrent_selections_have_atomic_record_and_replay_receipts(self):
        prompt = self.db.ensure_prompt("2026-03-29", "morning")
        barrier = Barrier(2, timeout=10)

        def select(index):
            store = Store(self.path, self.cfg)
            try:
                barrier.wait()
                return store.select(
                    f"concurrent-{index}",
                    1,
                    1,
                    None,
                    f"{prompt['id']}:{index}",
                    NOW,
                )
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(select, (0, 1)))
        self.assertEqual(len(self.db.records()), 1)
        saved = self.db.records()[0]
        self.assertIn(saved["watch_id"], self.cfg.watches)
        self.assertEqual(saved["watch"], self.cfg.watches[saved["watch_id"]])
        self.assertEqual(
            self.db.connection.execute("SELECT count(*) FROM callbacks").fetchone()[0],
            2,
        )
        before = self.db.export_csv()
        for index in (0, 1):
            reply = self.db.select(
                f"concurrent-{index}", 1, 1, None, f"{prompt['id']}:{index}", NOW
            )
            self.assertEqual(reply, replies[index])
        self.assertEqual(self.db.export_csv(), before)
