"""Cấu hình cho bridge.

Toàn bộ cấu hình đọc từ biến môi trường với tiền tố `AM_BRIDGE_` / `AM_UPSTREAM_`
(CLI trong `__main__` sẽ override lại). Không hard-code đường dẫn máy nào.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from typing import Mapping

# Upstream mặc định: MCP server đi kèm agentmemory (stdio) do npm phát hành.
DEFAULT_UPSTREAM_COMMAND = "npx"
DEFAULT_UPSTREAM_ARGS = "-y @agentmemory/mcp"


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_env_pairs(raw: str | None) -> dict[str, str]:
    """Phân tích `KEY=VALUE;KEY2=VALUE2` (hoặc phân tách bằng dấu phẩy)."""
    if not raw:
        return {}
    out: dict[str, str] = {}
    for chunk in raw.replace(",", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


@dataclass(slots=True)
class Config:
    # --- HTTP surface mà Freebuff kết nối tới ---
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/mcp"
    stateless: bool = False
    log_level: str = "info"

    # --- Upstream ---
    # Nếu đặt upstream_url: kết nối tới một MCP server HTTP có sẵn
    # (ví dụ chính agentmemory tại http://localhost:3111/mcp).
    # Ngược lại sẽ spawn upstream_command/upstream_args qua stdio.
    upstream_url: str | None = None
    upstream_command: str = DEFAULT_UPSTREAM_COMMAND
    upstream_args: list[str] = field(default_factory=lambda: shlex.split(DEFAULT_UPSTREAM_ARGS))
    upstream_env: dict[str, str] | None = None

    call_timeout: float = 120.0

    # --- Watchdog (giám sát upstream stdio) ---
    # ping_interval <= 0 → tắt watchdog.
    ping_interval: float = 30.0
    ping_timeout: float = 10.0
    ping_failures: int = 3

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        cfg = cls()

        cfg.host = env.get("AM_BRIDGE_HOST", cfg.host)
        if env.get("AM_BRIDGE_PORT"):
            cfg.port = int(env["AM_BRIDGE_PORT"])
        cfg.path = env.get("AM_BRIDGE_PATH", cfg.path)
        cfg.stateless = _truthy(env.get("AM_BRIDGE_STATELESS"), cfg.stateless)
        cfg.log_level = env.get("AM_BRIDGE_LOG_LEVEL", cfg.log_level)

        if env.get("AM_BRIDGE_CALL_TIMEOUT"):
            cfg.call_timeout = float(env["AM_BRIDGE_CALL_TIMEOUT"])

        if env.get("AM_BRIDGE_PING_INTERVAL"):
            cfg.ping_interval = float(env["AM_BRIDGE_PING_INTERVAL"])
        if env.get("AM_BRIDGE_PING_TIMEOUT"):
            cfg.ping_timeout = float(env["AM_BRIDGE_PING_TIMEOUT"])
        if env.get("AM_BRIDGE_PING_FAILURES"):
            cfg.ping_failures = max(1, int(env["AM_BRIDGE_PING_FAILURES"]))

        cfg.upstream_url = env.get("AM_UPSTREAM_URL") or None
        cfg.upstream_command = env.get("AM_UPSTREAM_COMMAND", cfg.upstream_command)
        if env.get("AM_UPSTREAM_ARGS") is not None:
            cfg.upstream_args = shlex.split(env["AM_UPSTREAM_ARGS"])

        overrides = _parse_env_pairs(env.get("AM_UPSTREAM_ENV"))
        if overrides:
            # Kế thừa môi trường hiện tại rồi mới áp override.
            cfg.upstream_env = {**os.environ, **overrides}

        return cfg

    @property
    def mcp_url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}{self.path}"

    def upstream_summary(self) -> str:
        if self.upstream_url:
            return self.upstream_url
        return " ".join([self.upstream_command, *self.upstream_args])
