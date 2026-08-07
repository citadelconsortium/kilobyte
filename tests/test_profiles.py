import unittest

from kilobyte.profiles import GENERAL, PROFILES, select


class ProfileSelectionTests(unittest.TestCase):
    def test_explicit_profile_wins(self):
        self.assertEqual(select("anything at all", explicit="security").name, "security")

    def test_keyword_routing(self):
        self.assertEqual(select("research the latest kernel CVEs").name, "research")
        self.assertEqual(select("there's a bug in my build, tests fail").name, "coding")
        self.assertEqual(select("run an nmap recon on the host").name, "security")
        self.assertEqual(select("why did the systemd service fail").name, "systems")

    def test_unclear_request_falls_back_to_general(self):
        self.assertEqual(select("tell me a joke").name, "general")

    def test_every_profile_has_grounding_language(self):
        # Each specialist must push toward evidence, not memory.
        for name, p in PROFILES.items():
            if name == "general":
                continue
            text = p.instructions.lower()
            self.assertTrue(
                any(w in text for w in ("evidence", "verify", "confirm", "tool", "run", "fetch", "source")),
                f"{name} profile lacks grounding language",
            )

    def test_general_is_the_default(self):
        self.assertIs(select(""), GENERAL)


if __name__ == "__main__":
    unittest.main()
