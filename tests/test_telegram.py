import json
import tempfile
import unittest
from pathlib import Path

from kilobyte.telegram import TelegramBridge


class TelegramTests(unittest.TestCase):
    def test_disabled_without_allowlist(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "telegram.json"
            path.write_text(json.dumps({"token": "secret", "allowed_chat_ids": []}))
            self.assertIsNone(TelegramBridge(path, object()).config())  # type: ignore[arg-type]

    def test_loads_explicit_allowlist(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "telegram.json"
            path.write_text(json.dumps({"token": "secret", "allowed_chat_ids": [42]}))
            self.assertEqual(TelegramBridge(path, object()).config()["allowed"], {42})  # type: ignore[union-attr,arg-type]


if __name__ == "__main__":
    unittest.main()

