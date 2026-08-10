from __future__ import annotations

import json
import os
import pwd
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Descriptive local filename doubles as the GitHub release asset name from brain-1.2 on;
# the legacy kilobyte.gguf name is still recognised for older installs.
MODEL_FILENAME = "kilobyte-4.1-3b-q4_k_m.gguf"
LEGACY_MODEL_FILENAME = "kilobyte.gguf"
MODEL_URL = "https://github.com/citadelconsortium/kilobyte/releases/download/brain-1.2/kilobyte-4.1-3b-q4_k_m.gguf"
MODEL_SHA256 = "72ec67bc6f964ce97f966cc83719100da00e058468aa0a5258cd7286a56cc8d2"
MODEL_REPOSITORY = "citadelconsortium/kilobyte (release brain-1.1)"
MODEL_QUANTIZATION = "Q4_K_M"


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def current_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: _env_path("KILOBYTE_DATA_DIR", "/var/lib/kilobyte"))
    config_dir: Path = field(default_factory=lambda: _env_path("KILOBYTE_CONFIG_DIR", "/etc/kilobyte"))
    runtime_dir: Path = field(default_factory=lambda: _env_path("KILOBYTE_RUNTIME_DIR", "/run/kilobyte"))
    log_dir: Path = field(default_factory=lambda: _env_path("KILOBYTE_LOG_DIR", "/var/log/kilobyte"))
    llama_binary: str = field(default_factory=lambda: os.environ.get("KILOBYTE_LLAMA_SERVER", "llama-server"))
    llama_host: str = "127.0.0.1"
    llama_port: int = field(default_factory=lambda: int(os.environ.get("KILOBYTE_LLAMA_PORT", "11435")))
    context_size: int = field(default_factory=lambda: int(os.environ.get("KILOBYTE_CONTEXT", "0")))
    max_agent_steps: int = 60
    max_output_tokens: int = 1536
    command_timeout: int = 120
    # Bytes captured from a subprocess; what actually reaches the model is bounded
    # separately by max_tool_result_tokens, because bytes are a poor proxy for context
    # cost -- dense output tokenises at about two characters per token.
    max_tool_output: int = 64 * 1024
    # Token allowance for a single tool result in the prompt. Kept well under the
    # context window so a large result cannot displace the conversation.
    max_tool_result_tokens: int = 900
    # Allowance for replayed conversation, so old turns cannot crowd out the current
    # task or the tool results it depends on.
    max_history_tokens: int = 1800
    max_read_bytes: int = 2 * 1024 * 1024
    memory_message_limit: int = 10_000
    memory_fact_limit: int = 2_000
    memory_skill_limit: int = 200
    reserve_memory_mb: int = 640
    home: Path = field(default_factory=current_home)

    @property
    def model_path(self) -> Path:
        """The brain the runtime loads.

        A promoted, trained brain lives in models/current/kilobyte.gguf and takes
        precedence, so `kilo brain promote` followed by a restart actually swaps the
        brain Kilo runs. With no promoted brain, the originally installed model is used,
        so a fresh install keeps working before any training has happened.
        """
        override = os.environ.get("KILOBYTE_MODEL_PATH")
        if override:
            return Path(override).expanduser()
        promoted = self.data_dir / "models" / "current" / "kilobyte.gguf"
        if promoted.is_file():
            return promoted
        named = self.data_dir / "models" / MODEL_FILENAME
        if named.is_file():
            return named
        legacy = self.data_dir / "models" / LEGACY_MODEL_FILENAME
        return legacy if legacy.is_file() else named

    @property
    def database_path(self) -> Path:
        return self.data_dir / "memory.sqlite3"

    @property
    def socket_path(self) -> Path:
        return self.runtime_dir / "kilobyte.sock"

    @property
    def policy_path(self) -> Path:
        return self.config_dir / "policy.json"

    @property
    def telegram_path(self) -> Path:
        return self.config_dir / "telegram.json"

    @property
    def mcp_path(self) -> Path:
        return self.config_dir / "mcp.json"

    @property
    def providers_path(self) -> Path:
        return self.config_dir / "providers.json"

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return (self.home.resolve(), Path("/tmp").resolve())

    def ensure_user_dirs(self) -> None:
        for path in (self.data_dir, self.runtime_dir, self.log_dir, self.data_dir / "models"):
            path.mkdir(parents=True, exist_ok=True)

    def load_json(self, path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return default
