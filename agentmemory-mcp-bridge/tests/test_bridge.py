"""End-to-end: mock upstream (stdio) → bridge (Streamable HTTP) → MCP client.

Chạy: `pytest -q` hoặc `python tests/test_bridge.py`.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp import Client

ROOT = Path(__file__).resolve().parent.parent
MOCK = Path(__file__).resolve().parent / "mock_upstream.py"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_healthy(port: int, timeout: float = 30.0) -> dict:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    import json

                    return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(0.25)
    raise AssertionError(f"bridge không healthy sau {timeout}s: {last}")


async def _exercise(port: int) -> None:
    async with Client(f"http://127.0.0.1:{port}/mcp") as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert {"echo", "add", "boom"} <= names, names

        echo = await client.call_tool("echo", {"text": "hi"})
        text = "".join(block.text for block in echo.content if getattr(block, "type", "") == "text")
        assert text == "echo: hi", text

        added = await client.call_tool("add", {"a": 2, "b": 3})
        assert not added.is_error

        boom = await client.call_tool("boom", {})
        assert boom.is_error, "tool lỗi phải trả is_error=True"


def _spawn(port: int, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    env = {
        **os.environ,
        "AM_BRIDGE_HOST": "127.0.0.1",
        "AM_BRIDGE_PORT": str(port),
        "AM_BRIDGE_LOG_LEVEL": "warning",
        "AM_UPSTREAM_COMMAND": sys.executable,
        "AM_UPSTREAM_ARGS": str(MOCK),
        **(extra_env or {}),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "agentmemory_bridge"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_bridge_end_to_end() -> None:
    port = _free_port()
    proc = _spawn(port)
    try:
        health = _wait_healthy(port)
        assert health["status"] == "ok"
        assert health["error"] is None, health
        assert health["tool_count"] == 3, health
        asyncio.run(_exercise(port))
    finally:
        _stop(proc)


def test_config_watchdog_env() -> None:
    """AM_BRIDGE_PING_* phải đọc được, kể cả giá trị 0 (tắt watchdog)."""
    from agentmemory_bridge.config import Config

    cfg = Config.from_env(
        {
            "AM_BRIDGE_PING_INTERVAL": "0",
            "AM_BRIDGE_PING_TIMEOUT": "2.5",
            "AM_BRIDGE_PING_FAILURES": "1",
        }
    )
    assert cfg.ping_interval == 0.0, cfg.ping_interval
    assert cfg.ping_timeout == 2.5, cfg.ping_timeout
    assert cfg.ping_failures == 1, cfg.ping_failures

    default = Config.from_env({})
    assert default.ping_interval > 0 and default.ping_failures >= 1


def test_upstream_death_makes_bridge_exit() -> None:
    """Upstream stdio chết → watchdog phải dừng bridge (để systemd restart).

    Đây là hành vi chống 'khỏe giả': trước đây /health vẫn trả ok với tool_count
    cũ dù tiến trình con đã chết.
    """
    port = _free_port()
    proc = _spawn(
        port,
        {
            "AM_BRIDGE_PING_INTERVAL": "1",
            "AM_BRIDGE_PING_TIMEOUT": "3",
            "AM_BRIDGE_PING_FAILURES": "2",
        },
    )
    try:
        _wait_healthy(port)
        subprocess.run(["pkill", "-f", str(MOCK)], check=False)
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                "bridge không tự thoát sau khi upstream chết (watchdog không hoạt động)"
            ) from None
        assert proc.returncode is not None, out
    finally:
        _stop(proc)


if __name__ == "__main__":
    test_config_watchdog_env()
    test_bridge_end_to_end()
    test_upstream_death_makes_bridge_exit()
    print("OK: bridge end-to-end")
