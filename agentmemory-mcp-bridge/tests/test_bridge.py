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


def test_bridge_end_to_end() -> None:
    port = _free_port()
    env = {
        **os.environ,
        "AM_BRIDGE_HOST": "127.0.0.1",
        "AM_BRIDGE_PORT": str(port),
        "AM_BRIDGE_LOG_LEVEL": "warning",
        "AM_UPSTREAM_COMMAND": sys.executable,
        "AM_UPSTREAM_ARGS": str(MOCK),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentmemory_bridge"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        health = _wait_healthy(port)
        assert health["status"] == "ok"
        assert health["tool_count"] == 3, health
        asyncio.run(_exercise(port))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    test_bridge_end_to_end()
    print("OK: bridge end-to-end")
