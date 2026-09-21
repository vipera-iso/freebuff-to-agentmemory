#!/bin/bash
set -e

echo "═══════════════════════════════════════════════════"
echo "  KIỂM TRA TIÊN QUYẾT HỆ THỐNG"
echo "═══════════════════════════════════════════════════"

ERRORS=0

# ── CPU ──
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 0)
if [ "$CPU_CORES" -lt 4 ]; then
    echo "❌ CPU: $CPU_CORES cores (cần ≥ 4)"
    ERRORS=$((ERRORS+1))
else
    echo "✅ CPU: $CPU_CORES cores"
fi

# ── RAM ──
RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0)
if [ "$RAM_GB" -lt 16 ]; then
    echo "❌ RAM: ${RAM_GB}GB (cần ≥ 16GB)"
    ERRORS=$((ERRORS+1))
else
    echo "✅ RAM: ${RAM_GB}GB"
fi

# ── Disk ──
DISK_GB=$(df -BG . 2>/dev/null | awk 'NR==2{print $4}' | tr -d 'G' || echo 0)
if [ "$DISK_GB" -lt 50 ]; then
    echo "❌ Disk: ${DISK_GB}GB trống (cần ≥ 50GB, khuyến nghị 80GB)"
    ERRORS=$((ERRORS+1))
else
    echo "✅ Disk: ${DISK_GB}GB trống"
fi

# ── Python ──
PYTHON_VER=$(python3 --version 2>/dev/null | grep -oP '\d+\.\d+' | head -1 || echo "0.0")
PY_MAJOR=$(echo "$PYTHON_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PYTHON_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]); then
    echo "❌ Python: $PYTHON_VER (cần ≥ 3.11)"
    ERRORS=$((ERRORS+1))
else
    echo "✅ Python: $PYTHON_VER"
fi

# ── Node.js ──
NODE_VER=$(node --version 2>/dev/null | grep -oP '\d+' | head -1 || echo 0)
if [ "$NODE_VER" -lt 22 ]; then
    echo "❌ Node.js: v$NODE_VER (cần ≥ 22)"
    ERRORS=$((ERRORS+1))
else
    echo "✅ Node.js: v$NODE_VER"
fi

# ── .NET SDK ──
if command -v dotnet &>/dev/null; then
    DOTNET_VER=$(dotnet --version 2>/dev/null | cut -d. -f1)
    if [ "$DOTNET_VER" -lt 10 ]; then
        echo "❌ .NET SDK: $DOTNET_VER (cần ≥ 10)"
        ERRORS=$((ERRORS+1))
    else
        echo "✅ .NET SDK: $DOTNET_VER"
    fi
else
    echo "❌ .NET SDK: chưa cài đặt (cần .NET 10 SDK)"
    ERRORS=$((ERRORS+1))
fi

# ── Docker ──
if command -v docker &>/dev/null; then
    DOCKER_VER=$(docker --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1)
    echo "✅ Docker: $DOCKER_VER"
else
    echo "❌ Docker: chưa cài đặt"
    ERRORS=$((ERRORS+1))
fi

# ── Docker Compose ──
if docker compose version &>/dev/null; then
    COMPOSE_VER=$(docker compose version --short 2>/dev/null)
    echo "✅ Docker Compose: $COMPOSE_VER"
else
    echo "❌ Docker Compose: chưa cài đặt"
    ERRORS=$((ERRORS+1))
fi

# ── Chrome/Chromium ──
if command -v google-chrome &>/dev/null || command -v chromium &>/dev/null || command -v chromium-browser &>/dev/null; then
    echo "✅ Chrome/Chromium: đã cài đặt"
else
    echo "⚠️  Chrome/Chromium: chưa tìm thấy (cần cho html-to-pptx)"
    ERRORS=$((ERRORS+1))
fi

# ── Kết quả ──
echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "🎉 TẤT CẢ TIÊN QUYẾT ĐẠT — Sẵn sàng cài đặt."
    exit 0
else
    echo "⚠️  CÓ $ERRORS VẤN ĐỀ — Sửa trước khi tiếp tục."
    exit 1
fi
