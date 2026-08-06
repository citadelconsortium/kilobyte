from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import ModelUnavailable, RuntimeUnavailable
from .resources import ResourceManager, ResourceProfile


class LlamaRuntime:
    """Owns exactly one persistent llama-server process and model instance."""

    def __init__(self, settings: Settings, resources: ResourceManager):
        self.settings = settings
        self.resources = resources
        self.profile: ResourceProfile | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.started_at: float | None = None
        self.log_path = settings.log_dir / "llama-server.log"
        self._log_handle: Any = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"http://{self.settings.llama_host}:{self.settings.llama_port}"

    def command(self, profile: ResourceProfile) -> list[str]:
        return [
            self.settings.llama_binary,
            "--model", str(self.settings.model_path),
            "--host", self.settings.llama_host,
            "--port", str(self.settings.llama_port),
            "--ctx-size", str(profile.context_size),
            "--threads", str(profile.threads),
            "--threads-batch", str(profile.threads),
            "--batch-size", str(profile.batch_size),
            "--ubatch-size", str(min(profile.batch_size, 128)),
            "--n-gpu-layers", str(profile.gpu_layers),
            "--parallel", "1",
            "--jinja",
            "--metrics",
            "--no-webui",
            "--chat-template-kwargs", '{"enable_thinking":false}',
        ]

    async def start(self, timeout: float = 240.0) -> None:
        async with self._lock:
            if self.process and self.process.returncode is None:
                return
            if not self.settings.model_path.is_file():
                raise ModelUnavailable(f"model not installed: {self.settings.model_path}")
            binary = shutil.which(self.settings.llama_binary)
            if not binary:
                raise RuntimeUnavailable(f"llama-server not found: {self.settings.llama_binary}")
            self.profile = self.resources.profile()
            enough, reason = self.resources.enough_to_start(self.profile)
            if not enough:
                raise RuntimeUnavailable(reason)
            self.settings.log_dir.mkdir(parents=True, exist_ok=True)
            if self._log_handle:
                self._log_handle.close()
            self._log_handle = self.log_path.open("ab", buffering=0)
            env = os.environ.copy()
            env.setdefault("LLAMA_CACHE", str(self.settings.data_dir / "cache"))
            self.process = await asyncio.create_subprocess_exec(
                *self.command(self.profile),
                stdout=self._log_handle,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
            self.started_at = time.monotonic()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process and self.process.returncode is not None:
                raise RuntimeUnavailable(f"llama-server exited {self.process.returncode}; see {self.log_path}")
            if await self.healthy():
                return
            await asyncio.sleep(0.5)
        await self.stop()
        raise RuntimeUnavailable(f"llama-server did not become healthy within {timeout:.0f}s")

    async def ensure_ready(self) -> None:
        """Recover a crashed model process while preserving the one-instance invariant."""
        if self.process is None or self.process.returncode is not None:
            await self.start()
            return
        headroom_ok, available_mb = self.resources.live_headroom()
        if not headroom_ok:
            raise RuntimeUnavailable(
                f"inference paused to protect the system: only {available_mb} MiB memory available"
            )

    async def stop(self) -> None:
        async with self._lock:
            process = self.process
            self.process = None
            if process and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    await asyncio.wait_for(process.wait(), timeout=15)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None

    async def healthy(self) -> bool:
        def check() -> bool:
            try:
                with urllib.request.urlopen(self.base_url + "/health", timeout=1.5) as response:
                    return response.status == 200
            except (OSError, urllib.error.URLError):
                return False
        return await asyncio.to_thread(check)

    async def warmup(self, system_prompt: str) -> None:
        """Pre-populate llama-server's KV cache with the system prompt so a user's first real
        message doesn't pay the full cold prompt-processing cost on slow CPUs."""
        payload = {
            "model": "kilobyte",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Reply with just: ready"}],
            "max_tokens": 4,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async for _ in self.chat_stream(payload):
            pass

    async def metadata(self) -> dict[str, Any]:
        def fetch() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(self.base_url + "/props", timeout=3) as response:
                    return json.load(response)
            except Exception:
                return {}
        return await asyncio.to_thread(fetch)

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Stream decoded OpenAI SSE deltas without exposing reasoning_content."""
        payload = dict(payload)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        def open_request():
            request = urllib.request.Request(
                self.base_url + "/v1/chat/completions",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            )
            return urllib.request.urlopen(request, timeout=600)

        try:
            response = await asyncio.to_thread(open_request)
        except Exception as exc:
            raise RuntimeUnavailable(f"inference request failed: {exc}") from exc
        try:
            while True:
                raw = await asyncio.to_thread(response.readline)
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or []
                if choices:
                    delta = dict(choices[0].get("delta") or {})
                    delta.pop("reasoning_content", None)
                    yield {"delta": delta, "finish_reason": choices[0].get("finish_reason")}
                if event.get("usage"):
                    yield {"usage": event["usage"]}
        finally:
            response.close()

    def status(self) -> dict[str, Any]:
        running = bool(self.process and self.process.returncode is None)
        return {
            "running": running,
            "pid": self.process.pid if running and self.process else None,
            "healthy": None,
            "uptime_seconds": int(time.monotonic() - self.started_at) if running and self.started_at else 0,
            "model": str(self.settings.model_path),
            "profile": self.profile.to_dict() if self.profile else None,
        }
