"""CLI: `python -m agentmemory_bridge` hoặc `agentmemory-mcp-bridge`."""

from __future__ import annotations

import argparse

from .bridge import serve
from .config import Config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agentmemory-mcp-bridge",
        description="Bọc MCP server của agentmemory và phơi ra qua Streamable HTTP cho Freebuff/OpenCode.",
    )
    parser.add_argument("--host", help="Host lắng nghe (mặc định 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port lắng nghe (mặc định 8765)")
    parser.add_argument("--path", help="Đường dẫn MCP (mặc định /mcp)")
    parser.add_argument(
        "--stateless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Bật/tắt phiên stateless cho Streamable HTTP",
    )
    parser.add_argument("--log-level", help="debug|info|warning|error")
    parser.add_argument("--upstream-url", help="MCP server HTTP có sẵn (bỏ qua command/args)")
    parser.add_argument("--upstream-command", help="Lệnh spawn upstream qua stdio (mặc định npx)")
    parser.add_argument("--upstream-args", help='Tham số upstream (mặc định "-y @agentmemory/mcp")')
    parser.add_argument("--call-timeout", type=float, help="Timeout gọi tool (giây)")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    if args.path is not None:
        config.path = args.path
    if args.stateless is not None:
        config.stateless = args.stateless
    if args.log_level is not None:
        config.log_level = args.log_level
    if args.upstream_url is not None:
        config.upstream_url = args.upstream_url
    if args.upstream_command is not None:
        config.upstream_command = args.upstream_command
    if args.upstream_args is not None:
        import shlex

        config.upstream_args = shlex.split(args.upstream_args)
    if args.call_timeout is not None:
        config.call_timeout = args.call_timeout

    serve(config)


if __name__ == "__main__":
    main()
