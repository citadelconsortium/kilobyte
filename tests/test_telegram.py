import json
import tempfile
import unittest
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from kilobyte.telegram import TelegramBridge


def _config(raw: str, payload: dict) -> Path:
    path = Path(raw) / "telegram.json"
    path.write_text(json.dumps(payload))
    return path


class TelegramConfigTests(unittest.TestCase):
    def test_disabled_without_allowlist(self):
        with tempfile.TemporaryDirectory() as raw:
            path = _config(raw, {"token": "secret", "allowed_chat_ids": []})
            self.assertIsNone(TelegramBridge(path, object()).config())  # type: ignore[arg-type]

    def test_disabled_without_token(self):
        with tempfile.TemporaryDirectory() as raw:
            path = _config(raw, {"token": "", "allowed_chat_ids": [42]})
            self.assertIsNone(TelegramBridge(path, object()).config())  # type: ignore[arg-type]

    def test_disabled_with_placeholder_token(self):
        """The shipped example must never accidentally authorise a live bot."""
        with tempfile.TemporaryDirectory() as raw:
            path = _config(raw, {"token": "PASTE_BOT_TOKEN_HERE", "allowed_chat_ids": [42]})
            self.assertIsNone(TelegramBridge(path, object()).config())  # type: ignore[arg-type]

    def test_disabled_when_missing_or_malformed(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assertIsNone(TelegramBridge(Path(raw) / "absent.json", object()).config())  # type: ignore[arg-type]
            bad = Path(raw) / "telegram.json"
            bad.write_text("{not json")
            self.assertIsNone(TelegramBridge(bad, object()).config())  # type: ignore[arg-type]
            ids = _config(raw, {"token": "secret", "allowed_chat_ids": ["not-an-id"]})
            self.assertIsNone(TelegramBridge(ids, object()).config())  # type: ignore[arg-type]

    def test_loads_explicit_allowlist(self):
        with tempfile.TemporaryDirectory() as raw:
            path = _config(raw, {"token": "secret", "allowed_chat_ids": [42]})
            self.assertEqual(TelegramBridge(path, object()).config()["allowed"], {42})  # type: ignore[union-attr,arg-type]


class TelegramDeliveryTests(IsolatedAsyncioTestCase):
    async def test_failure_is_reported_instead_of_silence(self):
        """A crash mid-generation must still produce a message; silence is
        indistinguishable from a hung bot."""

        class FailingAgent:
            def run(self, *args, **kwargs):
                async def generate():
                    raise RuntimeError("model unavailable")
                    yield  # pragma: no cover - makes this an async generator
                return generate()

        with tempfile.TemporaryDirectory() as raw:
            path = _config(raw, {"token": "secret", "allowed_chat_ids": [42]})
            bridge = TelegramBridge(path, FailingAgent())  # type: ignore[arg-type]
            sent: list[str] = []

            async def capture(token, chat_id, text):
                sent.append(text)

            bridge.send = capture  # type: ignore[method-assign]
            await bridge._reply("secret", 42, "hello")
            self.assertTrue(sent)
            self.assertIn("model unavailable", sent[0])


if __name__ == "__main__":
    unittest.main()
