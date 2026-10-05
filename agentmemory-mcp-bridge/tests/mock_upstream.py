"""Upstream MCP server giả lập (stdio) — dùng cho test, không cần agentmemory thật."""

from __future__ import annotations

from mcp.server import MCPServer

mcp = MCPServer("mock-agentmemory", version="0.0.1")


@mcp.tool()
def echo(text: str) -> str:
    """Trả lại nguyên văn đoạn text được truyền vào."""
    return f"echo: {text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """Cộng hai số nguyên."""
    return a + b


@mcp.tool()
def boom() -> str:
    """Tool luôn ném lỗi, để kiểm tra đường truyền lỗi."""
    raise RuntimeError("mock upstream boom")


if __name__ == "__main__":
    mcp.run("stdio")
