# agentmemory-mcp-bridge

Bọc MCP server **stdio** của [agentmemory](https://github.com/rohitg00/agentmemory)
(`@agentmemory/mcp`) thành **Streamable HTTP**, để **Freebuff** và **OpenCode**
dùng chung một bộ nhớ dài hạn.

## Vì sao cần bridge?

`@agentmemory/mcp` chỉ nói **stdio** (một tiến trình con). Freebuff cấu hình MCP
qua `.agents/mcp.json` với `type: "http"`, OpenCode qua `opencode.json` với
`type: "remote"` — cả hai đều kết nối bằng **URL**. Bridge này là lớp chuyển
transport, **giữ nguyên** tên/schema tool của agentmemory (dùng tầng thấp
`mcp.server.Server`, không sinh lại schema từ type hints).

```
Freebuff (.agents/mcp.json)  ┐
                             ├─ Streamable HTTP ─▶ bridge :8765/mcp ─ stdio ─▶ @agentmemory/mcp ─ REST ─▶ agentmemory :3111
OpenCode (opencode.json)     ┘
```

## Cài đặt

```bash
cd ~/agentmemory-mcp-bridge
uv venv .venv
uv pip install -e .
chmod +x scripts/run-bridge.sh
```

## Chạy

```bash
# Cách 1: script (tự thêm PATH của npx, hợp với systemd)
./scripts/run-bridge.sh

# Cách 2: trực tiếp
.venv/bin/python -m agentmemory_bridge
```

Kiểm tra readiness:

```bash
curl -fsS http://127.0.0.1:8765/health | python -m json.tool
# {"status":"ok","upstream":"npx -y @agentmemory/mcp","upstream_server":{...},"tool_count":7,"mcp_url":"http://127.0.0.1:8765/mcp"}
```

### Dịch vụ systemd (user)

```bash
systemctl --user daemon-reload
systemctl --user enable --now agentmemory-mcp-bridge.service
journalctl --user -u agentmemory-mcp-bridge -f      # hoặc: tail -f /tmp/agentmemory-mcp-bridge.log
systemctl --user status agentmemory-mcp-bridge.service
```

## Kết nối agent

### Freebuff — `.agents/mcp.json`

Đặt tại **gốc project** (`<project>/.agents/mcp.json`):

```json
{
  "mcpServers": {
    "agentmemory": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

> ⚠️ Freebuff load MCP config **một lần lúc khởi động** từ `process.cwd()`.
> Phải chạy `cd <project> && freebuff` (không chọn project qua picker), nếu không
> `mcpServers` sẽ không được nạp (xem CodebuffAI/freebuff#957).

### OpenCode — `~/.config/opencode/opencode.json`

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "agentmemory": {
      "type": "remote",
      "url": "http://127.0.0.1:8765/mcp",
      "enabled": true
    }
  }
}
```

Xác nhận: `opencode debug config` phải thấy `mcp.agentmemory`.

## Cấu hình (biến môi trường)

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `AM_BRIDGE_HOST` | `127.0.0.1` | Host lắng nghe |
| `AM_BRIDGE_PORT` | `8765` | Port lắng nghe |
| `AM_BRIDGE_PATH` | `/mcp` | Đường dẫn MCP |
| `AM_BRIDGE_STATELESS` | `0` | `1` = stateless HTTP (bỏ phiên) |
| `AM_BRIDGE_LOG_LEVEL` | `info` | `debug`/`info`/`warning`/`error` |
| `AM_BRIDGE_CALL_TIMEOUT` | `120` | Timeout gọi tool (giây) |
| `AM_UPSTREAM_COMMAND` | `npx` | Lệnh spawn upstream (stdio) |
| `AM_UPSTREAM_ARGS` | `-y @agentmemory/mcp` | Tham số upstream |
| `AM_UPSTREAM_ENV` | – | `KEY=VALUE;KEY2=VALUE2` thêm vào môi trường upstream |
| `AM_UPSTREAM_URL` | – | Nếu đặt: nối tới MCP server HTTP có sẵn thay vì spawn stdio |

Tương đương bằng cờ CLI: `--host/--port/--path/--stateless/--upstream-url/...`
(xem `python -m agentmemory_bridge --help`).

## Bộ nhớ đầy đủ (54 tool)

Bridge chạy được ngay cả khi **server agentmemory chưa bật**: `@agentmemory/mcp`
rơi vào chế độ `LOCAL FALLBACK` với **7/54 tool** (`memory_recall`, `memory_save`,
`memory_smart_search`, `memory_sessions`, `memory_export`, `memory_audit`,
`memory_governance_delete`). Để có đủ 54 tool và bộ nhớ **bền vững**, chạy server:

```bash
npx -y @agentmemory/agentmemory@latest      # REST/MCP :3111, viewer :3113
```

Bridge sẽ tự nhận server qua `http://localhost:3111` (hoặc đặt `AM_UPSTREAM_ENV=AGENTMEMORY_URL=http://localhost:3111`).

## Kiến trúc & kiểm thử

- `agentmemory_bridge/config.py` — cấu hình env/CLI.
- `agentmemory_bridge/bridge.py` — lifespan giữ upstream, forward `tools`/`resources`/`prompts`.
- `agentmemory_bridge/__main__.py` — CLI.
- `tests/mock_upstream.py` + `tests/test_bridge.py` — end-to-end upstream giả lập → HTTP → client
  (list tools, gọi tool thành công, và đường truyền lỗi `is_error`).

```bash
.venv/bin/python -m pytest -q
# hoặc
.venv/bin/python tests/test_bridge.py
```

## Migration Guide

- **Không sửa schema**: mọi tool của agentmemory đi qua nguyên vẹn vì bridge dùng
  tầng thấp, không suy schema từ type hints.
- **Lỗi tool** trả về dạng `is_error=True` kèm thông điệp, để model đọc và thử lại,
  thay vì làm gãy cả phiên.
