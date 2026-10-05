#!/usr/bin/env bash
# Cầu nối @agentmemory/mcp (stdio) → Streamable HTTP cho Freebuff + OpenCode.
#
# Vì sao cần: `@agentmemory/mcp` chỉ nói **stdio**, còn Freebuff (`.agents/mcp.json`
# với `type: http`) và OpenCode (`type: remote`) lại kết nối bằng **HTTP**.
# Bridge này bọc upstream stdio thành Streamable HTTP tại http://127.0.0.1:8765/mcp
# và giữ nguyên toàn bộ tool/schema của agentmemory.
#
# Override qua env (xem README): AM_BRIDGE_*, AM_UPSTREAM_*.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_ROOT/.venv/bin/python"

# Dưới systemd, PATH tối giản không có nvm → tự thêm bin dir của Node (npx).
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
  echo "Không tìm thấy npx (cần Node.js ≥ 20). Cài Node hoặc đặt PATH thủ công." >&2
  exit 1
fi

if [ ! -x "$VENV_PY" ]; then
  echo "Chưa có .venv tại $REPO_ROOT. Chạy trước:" >&2
  echo "  cd $REPO_ROOT && uv venv .venv && uv pip install -e ." >&2
  exit 1
fi

exec "$VENV_PY" -m agentmemory_bridge "$@"
