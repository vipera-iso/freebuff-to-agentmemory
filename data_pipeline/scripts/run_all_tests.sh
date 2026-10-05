#!/bin/bash
# scripts/run_all_tests.sh — Chạy toàn bộ test pipeline theo TEST_PROCEDURE.md
set -e

echo "═══════════════════════════════════════"
echo "  CHẠY TOÀN BỘ TEST PIPELINE"
echo "═══════════════════════════════════════"

# Log ra file với timestamp
LOG_FILE="logs/test_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs
exec > >(tee -a "$LOG_FILE") 2>&1

# Venv Python 3.12 (bắt buộc — máy có 3.14 ngoài vùng hỗ trợ Unstructured)
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
else
    echo "⚠️  Không thấy .venv — đang dùng python hệ thống (cảnh báo: Unstructured cần Python 3.11–3.13)"
fi

echo "[TEST 1] Prerequisites"
./scripts/check_prerequisites.sh || exit 1

echo "[TEST 2] Bifrost Gateway"
curl -sf http://localhost:8080/health || { echo "❌ Bifrost :8080 không phản hồi"; exit 1; }

echo "[TEST 3] Codex Relay (env cô lập CODEX_HOME)"
curl -sf http://127.0.0.1:4444/v1/models > /dev/null || { echo "❌ Relay :4444 không phản hồi — khởi động codex-relay trước (TEST_PROCEDURE.md TEST 3.1)"; exit 1; }

echo "[TEST 4] pptx-tools MCP (bridge stdio→HTTP)"
curl -sf http://localhost:3001/health > /dev/null || { echo "❌ pptx-tools :3001 không phản hồi — chạy scripts/pptx_mcp_bridge.sh (TEST_PROCEDURE.md TEST 4.1)"; exit 1; }

echo "[TEST 5-11] Chạy pytest toàn bộ"
python -m pytest tests/ -v --tb=short || exit 1

echo ""
echo "🎉 TẤT CẢ TEST PASS — log: $LOG_FILE"
