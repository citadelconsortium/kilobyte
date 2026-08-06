import tempfile
import unittest
from pathlib import Path

from kilobyte.memory import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.tmp.name) / "memory.db", message_limit=3, fact_limit=2)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_separate_sessions_and_bounded_messages(self):
        first = self.store.new_session("terminal")
        second = self.store.new_session("terminal")
        for index in range(4):
            self.store.add_message(first, "user", f"message-{index}")
        self.store.add_message(second, "user", "separate")
        self.assertNotIn("message-0", str(self.store.history(first)))
        self.assertEqual(self.store.history(second)[0]["content"], "separate")
        self.assertLessEqual(self.store.stats()["messages"], 3)

    def test_recall_and_fact_bound(self):
        self.store.remember("prefers concise terminal output", importance=0.9)
        self.store.remember("uses Arch Linux", importance=0.8)
        self.store.remember("temporary low value", importance=0.1)
        self.assertIn("Arch Linux", " ".join(self.store.recall("Which Linux does the user use?")))
        self.assertLessEqual(self.store.stats()["facts"], 2)


if __name__ == "__main__":
    unittest.main()

