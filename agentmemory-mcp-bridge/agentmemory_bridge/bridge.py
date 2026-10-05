"""Bridge: bọc upstream MCP server (agentmemory) và phơi ra qua Streamable HTTP.

Luồng:  Freebuff / OpenCode  ──Streamable HTTP──▶  bridge  ──(stdio|HTTP)──▶  @agentmemory/mcp  ──REST──▶  agentmemory (:3111)

Bridge dùng tầng thấp (`mcp.server.Server`) để **giữ nguyên** schema tool của
upstream (không sinh lại schema từ type hints), nhờ vậy mọi tool của
agentmemory (memory_save, memory_smart_search, memory_recall, …) đi qua nguyên vẹn.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Callable

from mcp import Client, MCPError, StdioServerParameters
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    EmptyResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    TextContent,
)

from . import __version__
from .config import Config

logger = logging.getLogger("agentmemory_bridge")


@dataclass
class BridgeState:
    """Trạng thái upstream, chia sẻ giữa lifespan và mọi request (kể cả /health)."""

    client: Client | None = None
    server_info: dict[str, Any] | None = None
    tool_count: int | None = None
    connected: bool = False
    error: str | None = None
    # Được serve() gán vào: yêu cầu uvicorn dừng sạch để systemd restart
    # (Restart=always) — cách hồi phục duy nhất khi upstream stdio đã chết
    # mà tiến trình bridge còn sống.
    request_shutdown: Callable[[], None] | None = None


def _build_client(config: Config) -> Client:
    if config.upstream_url:
        return Client(config.upstream_url, read_timeout_seconds=config.call_timeout)
    params = StdioServerParameters(
        command=config.upstream_command,
        args=list(config.upstream_args),
        env=config.upstream_env,
    )
    return Client(params, read_timeout_seconds=config.call_timeout)


async def _watchdog(config: Config, state: BridgeState) -> None:
    """Giám sát upstream stdio bằng `list_tools()` làm nhịp tim.

    MCP đã gỡ `ping` (2026-07-28, chỉ còn trong `mode='legacy'`), nên dùng
    `list_tools()` — vừa là request thật qua kênh stdio, vừa cập nhật
    `tool_count`. Sau `ping_failures` lần liên tiếp thất bại thì yêu cầu uvicorn
    dừng sạch: tiến trình thoát, systemd (`Restart=always`) dựng lại bridge với
    upstream mới — không còn cảnh `/health` trả `ok` trong khi upstream đã chết.
    """
    if config.ping_interval <= 0:
        logger.info("watchdog tắt (AM_BRIDGE_PING_INTERVAL=0)")
        return

    failures = 0
    while True:
        await asyncio.sleep(config.ping_interval)
        client = state.client
        if client is None:
            continue
        try:
            tools = await asyncio.wait_for(client.list_tools(), timeout=config.ping_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - watchdog nuốt mọi lỗi để tiếp tục
            failures += 1
            state.error = (
                f"upstream không trả lời ({failures}/{config.ping_failures}): "
                f"{type(exc).__name__}: {exc}"
            )
            logger.warning("watchdog: %s", state.error)
            if failures < config.ping_failures:
                continue

            state.connected = False
            if state.request_shutdown is None:
                logger.error("upstream chết nhưng bridge không có request_shutdown — /health trả 503")
                return
            logger.error(
                "upstream chết %d lần liên tiếp — dừng bridge để systemd restart",
                failures,
            )
            state.request_shutdown()
            return

        failures = 0
        state.error = None
        state.tool_count = len(tools.tools)


def _make_lifespan(config: Config, state: BridgeState):
    @asynccontextmanager
    async def lifespan(_server: Server[BridgeState]):
        async with AsyncExitStack() as stack:
            client = _build_client(config)
            await stack.enter_async_context(client)

            state.client = client
            state.connected = True
            state.error = None
            if client.server_info is not None:
                state.server_info = client.server_info.model_dump(exclude_none=True)

            try:
                tools = await client.list_tools()
                state.tool_count = len(tools.tools)
            except Exception as exc:  # upstream sống nhưng tools/list lỗi
                logger.warning("upstream tools/list thất bại: %s", exc)

            watchdog = asyncio.create_task(
                _watchdog(config, state), name="agentmemory-bridge-watchdog"
            )

            logger.info(
                "upstream sẵn sàng (%s) — %s tool(s)",
                config.upstream_summary(),
                state.tool_count,
            )
            try:
                yield state
            finally:
                watchdog.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog
                state.connected = False
                state.client = None
                state.error = None

    return lifespan


def _client_of(ctx: ServerRequestContext[BridgeState, Any]) -> Client:
    client = ctx.lifespan_context.client
    if client is None:
        raise MCPError(-32000, "upstream chưa kết nối")
    return client


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        is_error=True,
    )


def _make_handlers():
    async def on_list_tools(
        ctx: ServerRequestContext[BridgeState, Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return await _client_of(ctx).list_tools(cursor=getattr(params, "cursor", None))

    async def on_call_tool(
        ctx: ServerRequestContext[BridgeState, Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        try:
            return await _client_of(ctx).call_tool(params.name, params.arguments)
        except MCPError as exc:
            return _error_result(f"agentmemory upstream error: {exc}")
        except Exception as exc:  # noqa: BLE001 - trả lỗi cho model thay vì crash
            logger.exception("call_tool %r thất bại", params.name)
            return _error_result(f"{type(exc).__name__}: {exc}")

    async def on_list_resources(
        ctx: ServerRequestContext[BridgeState, Any],
        params: PaginatedRequestParams | None,
    ) -> ListResourcesResult:
        try:
            return await _client_of(ctx).list_resources(cursor=getattr(params, "cursor", None))
        except Exception:  # upstream không hỗ trợ resources
            return ListResourcesResult(resources=[])

    async def on_list_resource_templates(
        ctx: ServerRequestContext[BridgeState, Any],
        params: PaginatedRequestParams | None,
    ) -> ListResourceTemplatesResult:
        try:
            return await _client_of(ctx).list_resource_templates(
                cursor=getattr(params, "cursor", None)
            )
        except Exception:
            return ListResourceTemplatesResult(resource_templates=[])

    async def on_read_resource(
        ctx: ServerRequestContext[BridgeState, Any],
        params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        return await _client_of(ctx).read_resource(params.uri)

    async def on_list_prompts(
        ctx: ServerRequestContext[BridgeState, Any],
        params: PaginatedRequestParams | None,
    ) -> ListPromptsResult:
        try:
            return await _client_of(ctx).list_prompts(cursor=getattr(params, "cursor", None))
        except Exception:
            return ListPromptsResult(prompts=[])

    async def on_get_prompt(
        ctx: ServerRequestContext[BridgeState, Any],
        params: GetPromptRequestParams,
    ) -> GetPromptResult:
        return await _client_of(ctx).get_prompt(params.name, params.arguments)

    return {
        "on_list_tools": on_list_tools,
        "on_call_tool": on_call_tool,
        "on_list_resources": on_list_resources,
        "on_list_resource_templates": on_list_resource_templates,
        "on_read_resource": on_read_resource,
        "on_list_prompts": on_list_prompts,
        "on_get_prompt": on_get_prompt,
    }


def build_server(config: Config) -> tuple[Server[BridgeState], BridgeState]:
    state = BridgeState()
    server: Server[BridgeState] = Server(
        name="agentmemory-bridge",
        version=__version__,
        title="agentmemory MCP bridge",
        instructions=(
            "Cầu nối tới agentmemory — bộ nhớ dài hạn cho coding agent. "
            "Dùng memory_smart_search / memory_recall để tìm lại ngữ cảnh cũ, "
            "memory_save để ghi lại điều đáng nhớ."
        ),
        lifespan=_make_lifespan(config, state),
        **_make_handlers(),
    )
    return server, state


def build_app(config: Config) -> tuple[Any, BridgeState]:
    """Trả về `(Starlette ASGI app, BridgeState)` — app gồm Streamable HTTP + /health.

    Trả kèm `state` để `serve()` gán `state.request_shutdown` (watchdog dùng khi
    upstream chết) — nếu chỉ trả app thì không có cách nào chạm tới state.
    """
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    server, state = build_server(config)

    async def health(_request: Request) -> JSONResponse:
        if state.connected and state.error:
            status = "degraded"  # vẫn phục vụ được, nhưng upstream đang chập chờn
        elif state.connected:
            status = "ok"
        else:
            status = "down" if state.error else "starting"
        payload = {
            "status": status,
            "error": state.error,
            "upstream": config.upstream_summary(),
            "upstream_server": state.server_info,
            "tool_count": state.tool_count,
            "mcp_url": config.mcp_url,
        }
        return JSONResponse(payload, status_code=200 if state.connected else 503)

    app = server.streamable_http_app(
        streamable_http_path=config.path,
        stateless_http=config.stateless,
        host=config.host,
        custom_starlette_routes=[Route("/health", health)],
    )
    return app, state


def serve(config: Config) -> None:
    logging.basicConfig(
        level=config.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import uvicorn

    app, state = build_app(config)
    uv_config = uvicorn.Config(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        # Watchdog có thể yêu cầu dừng khi upstream chết; đừng kẹt vô hạn
        # trên các kết nối keep-alive cũ (systemd sẽ dựng lại sau RestartSec).
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(uv_config)
    state.request_shutdown = lambda: setattr(server, "should_exit", True)

    logger.info("bridge nghe tại %s (upstream: %s)", config.mcp_url, config.upstream_summary())
    server.run()
