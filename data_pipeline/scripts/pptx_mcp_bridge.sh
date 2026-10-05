#!/usr/bin/env bash
# Cầu nối stdio → Streamable HTTP cho pptx-tools MCP server.
#
# Vấn đề: upstream jongalloway/pptx-tools chỉ hỗ trợ **stdio transport**
# (Program.cs dùng `WithStdioServerTransport()`, chạy với cờ `--stdio`).
# Pipeline/Bifrost lại kết nối qua HTTP tại http://localhost:3001/mcp,
# nên cần lớp bridge này (supergateway) bọc stdio thành Streamable HTTP.
#
# Env override:
#   PPTX_TOOLS_DLL  - đường dẫn PptxTools.dll (mặc định build Release trong repos/)
#   PPTX_TOOLS_ROOT - thư mục .NET (mặc định $HOME/.dotnet)
#   PPTX_MCP_PORT   - port HTTP (mặc định 3001)
#   PPTX_MCP_PATH   - path Streamable HTTP (mặc định /mcp)
set -euo pipefail

PPTX_TOOLS_ROOT="${PPTX_TOOLS_ROOT:-$HOME/.dotnet}"
export DOTNET_ROOT="$PPTX_TOOLS_ROOT"
export PATH="$PPTX_TOOLS_ROOT:$PATH"

# supergateway chạy qua npx (Node). Dưới systemd, PATH tối giản không có nvm,
# nên tự thêm bin dir của Node nếu `npx` chưa có mặt.
if ! command -v npx >/dev/null 2>&1; then
  for _node_bin in "$HOME/.nvm/versions/node"/v*/bin; do
    if [ -x "$_node_bin/npx" ]; then
      PATH="$_node_bin:$PATH"
      break
    fi
  done
  export PATH
fi
if ! command -v npx >/dev/null 2>&1; then
  echo "Không tìm thấy npx (Node.js ≥ 22). Cài Node hoặc đặt NODE_BIN." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PPTX_TOOLS_DLL="${PPTX_TOOLS_DLL:-$REPO_ROOT/repos/pptx-tools/src/PptxTools/bin/Release/net10.0/PptxTools.dll}"
PPTX_MCP_PORT="${PPTX_MCP_PORT:-3001}"
PPTX_MCP_PATH="${PPTX_MCP_PATH:-/mcp}"
SUPERGATEWAY_VERSION="${SUPERGATEWAY_VERSION:-4.1.0}"

if [ ! -f "$PPTX_TOOLS_DLL" ]; then
  echo "Không tìm thấy PptxTools.dll: $PPTX_TOOLS_DLL" >&2
  echo "Build trước: dotnet build $REPO_ROOT/repos/pptx-tools/PptxTools.slnx --configuration Release" >&2
  exit 1
fi

# --stateful: giữ session (Bifrost config cần needs_session_stickiness=true).
# --healthEndpoint /health: cho phép kiểm tra readiness bằng curl.
exec npx -y "supergateway@${SUPERGATEWAY_VERSION}" \
  --stdio "dotnet $PPTX_TOOLS_DLL --stdio" \
  --outputTransport streamableHttp \
  --port "$PPTX_MCP_PORT" \
  --streamableHttpPath "$PPTX_MCP_PATH" \
  --stateful \
  --healthEndpoint /health \
  --logLevel info
