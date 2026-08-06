import unittest
from pathlib import Path

from kilobyte.config import MODEL_SHA256, MODEL_URL


class InstallationTests(unittest.TestCase):
    def test_model_is_pinned_and_atomic_installer(self):
        script = (Path(__file__).parents[1] / "scripts" / "install-model.sh").read_text()
        self.assertIn(MODEL_SHA256, script)
        self.assertIn(".part", script)
        self.assertIn("sha256sum --check", script)
        self.assertIn("mv -f", script)
        self.assertIn("Qwen3-1.7B-Q4_K_M.gguf", MODEL_URL)

    def test_service_uses_one_daemon(self):
        unit = (Path(__file__).parents[1] / "systemd" / "kilobyte.service").read_text()
        self.assertIn("kilobyte.daemon", unit)
        self.assertIn("Restart=on-failure", unit)


if __name__ == "__main__":
    unittest.main()

