#!/usr/bin/env bash
# Khởi động agentmemory server (REST :3111) qua npx.
#
# Vì sao cần script thay vì ghi thẳng vào unit:
#   - systemd không hỗ trợ glob trong Environment/ExecStart, nên không thể hard-code
#     ~/.nvm/versions/node/v24.21.0/bin — nâng Node là unit chết ngay. Ở đây tự quét
#     mọi phiên bản nvm như scripts/run-bridge.sh của bridge.
#   - Ghim version (không dùng @latest): `Restart=always` sẽ chạy lại lệnh này sau
#     mỗi lần crash, @latest nghĩa là cứ crash một lần là kéo mã nguồn mới về chạy —
#     không reproduce được và tăng bề mặt supply-chain.
set -euo pipefail

# Nâng cấp: AGENTMEMORY_VERSION=x.y.z systemctl --user restart agentmemory
VERSION="${AGENTMEMORY_VERSION:-0.9.29}"

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

exec npx -y "@agentmemory/agentmemory@${VERSION}"
