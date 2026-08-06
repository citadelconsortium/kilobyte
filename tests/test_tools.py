import tempfile
import unittest
from pathlib import Path

from kilobyte.config import Settings
from kilobyte.errors import SecurityError
from kilobyte.memory import MemoryStore
from kilobyte.security import PermissionManager
from kilobyte.tools import ToolContext, ToolRegistry


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = Settings(data_dir=root, config_dir=root, runtime_dir=root, log_dir=root, home=root)
        self.memory = MemoryStore(root / "memory.db")
        self.tools = ToolRegistry(self.settings, self.memory, PermissionManager(root / "policy.json"))
        self.session = self.memory.new_session()
        self.context = ToolContext(self.session, root)

    async def asyncTearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    async def test_read_list_and_command(self):
        path = Path(self.tmp.name) / "hello.txt"
        path.write_text("hello Kilobyte", encoding="utf-8")
        result = await self.tools.execute("read_file", {"path": str(path)}, self.context)
        self.assertEqual(result["content"], "hello Kilobyte")
        command = await self.tools.execute("run_command", {"command": "/usr/bin/printf okay"}, self.context)
        self.assertEqual(command["stdout"], "okay")

    async def test_remote_mutation_is_absent_and_blocked(self):
        names = {item["function"]["name"] for item in self.tools.schemas(remote=True)}
        self.assertNotIn("write_file", names)
        self.assertNotIn("run_command", names)
        with self.assertRaises(SecurityError):
            await self.tools.execute("run_command", {"command": "true"}, ToolContext(self.session, Path(self.tmp.name), remote=True))

    async def test_request_routes_only_relevant_tool_schemas(self):
        self.assertEqual(self.tools.schemas(request="Reply with exactly: ready"), [])
        system_names = {item["function"]["name"] for item in self.tools.schemas(request="Inspect this machine CPU")}
        self.assertEqual(system_names, {"system_info"})
        web_names = {item["function"]["name"] for item in self.tools.schemas(remote=True, request="Search the web for Arch Linux")}
        self.assertEqual(web_names, {"web_search", "web_fetch"})

    async def test_memory_tools(self):
        await self.tools.execute("remember", {"content": "favorite shell is bash"}, self.context)
        result = await self.tools.execute("recall", {"query": "favorite shell"}, self.context)
        self.assertTrue(result["facts"])

    async def test_web_search_parses_bounded_rss(self):
        rss = """<?xml version="1.0"?><rss><channel><item><title>Arch Linux</title><link>https://archlinux.org/</link><description>Simple &amp; lightweight.</description></item></channel></rss>"""
        results = self.tools._parse_search_rss(rss, 2)
        self.assertEqual(results[0]["url"], "https://archlinux.org/")
        self.assertEqual(results[0]["snippet"], "Simple & lightweight.")


if __name__ == "__main__":
    unittest.main()
