import json
import tempfile
import unittest
from pathlib import Path

from kilobyte.agent import Agent
from kilobyte.config import Settings
from kilobyte.memory import MemoryStore
from kilobyte.security import PermissionManager
from kilobyte.tools import ToolRegistry


class FakeRuntime:
    def __init__(self):
        self.calls = 0
        self.ready_checks = 0

    async def ensure_ready(self):
        self.ready_checks += 1

    async def chat_stream(self, payload):
        self.calls += 1
        if self.calls == 1:
            yield {"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "system_info", "arguments": "{}"}}]}}
        else:
            for token in ("Machine ", "checked."):
                yield {"delta": {"content": token}}
            yield {"usage": {"completion_tokens": 2}}


class CapturingRuntime:
    def __init__(self):
        self.payload = None

    async def ensure_ready(self):
        pass

    async def chat_stream(self, payload):
        self.payload = payload
        yield {"delta": {"content": "ready"}}


class DuplicateToolRuntime:
    def __init__(self):
        self.payloads = []

    async def ensure_ready(self):
        pass

    async def chat_stream(self, payload):
        self.payloads.append(payload)
        if len(self.payloads) <= 2:
            yield {"delta": {"tool_calls": [{"index": 0, "id": f"call-{len(self.payloads)}", "function": {"name": "system_info", "arguments": "{}"}}]}}
        else:
            yield {"delta": {"content": "Linux, 2 CPUs"}}


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_loop_streams_and_persists(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = Settings(data_dir=root, config_dir=root, runtime_dir=root, log_dir=root, home=root)
            memory = MemoryStore(root / "memory.db")
            tools = ToolRegistry(settings, memory, PermissionManager(root / "policy.json"))
            runtime = FakeRuntime()
            agent = Agent(settings, runtime, memory, tools)  # type: ignore[arg-type]
            events = [event async for event in agent.run("Check this machine")]
            self.assertEqual("".join(e.get("text", "") for e in events), "Machine checked.")
            self.assertTrue(any(e["type"] == "tool_end" and e["ok"] for e in events))
            self.assertEqual(runtime.calls, 2)
            self.assertEqual(runtime.ready_checks, 2)
            self.assertEqual(memory.stats()["tool_audit"], 1)
            memory.close()

    async def test_plain_chat_sends_the_stable_tool_schema(self):
        """A plain answer still carries the full tool list: the prefix has to stay
        identical between requests for llama-server's prompt cache to be reused."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = Settings(data_dir=root, config_dir=root, runtime_dir=root, log_dir=root, home=root)
            memory = MemoryStore(root / "memory.db")
            tools = ToolRegistry(settings, memory, PermissionManager(root / "policy.json"))
            runtime = CapturingRuntime()
            agent = Agent(settings, runtime, memory, tools)  # type: ignore[arg-type]
            events = [event async for event in agent.run("Reply with exactly: ready")]
            self.assertEqual("".join(e.get("text", "") for e in events), "ready")
            self.assertEqual(runtime.payload["tools"], tools.schemas())
            memory.close()

    async def test_duplicate_tool_call_is_blocked_and_tools_are_disabled(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = Settings(data_dir=root, config_dir=root, runtime_dir=root, log_dir=root, home=root)
            memory = MemoryStore(root / "memory.db")
            tools = ToolRegistry(settings, memory, PermissionManager(root / "policy.json"))
            runtime = DuplicateToolRuntime()
            agent = Agent(settings, runtime, memory, tools)  # type: ignore[arg-type]
            events = [event async for event in agent.run("Inspect this machine CPU")]
            self.assertEqual("".join(e.get("text", "") for e in events), "Linux, 2 CPUs")
            self.assertEqual(memory.stats()["tool_audit"], 1)
            self.assertNotIn("tools", runtime.payloads[2])
            self.assertTrue(any(e["type"] == "tool_end" and not e["ok"] for e in events))
            memory.close()


if __name__ == "__main__":
    unittest.main()
