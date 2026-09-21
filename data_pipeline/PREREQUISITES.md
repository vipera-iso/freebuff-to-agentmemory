# PREREQUISITES — Kiểm tra tiên quyết trước khi cài đặt

> **Mục đích:** Xác minh máy chủ đáp ứng đủ tài nguyên và phần mềm trước khi bắt đầu cài đặt pipeline.
> **Chạy script này trước khi thực thi bất kỳ giai đoạn nào.**

---

## 0. Quyết định kiến trúc đã chốt (2026-09-21)

| Quyết định | Lý do |
|---|---|
| **Qdrant + LlamaIndex** thay RAGFlow | Máy 14 GB RAM < 16 GB yêu cầu của RAGFlow. Qdrant embedded mode không cần Docker, nhẹ hơn nhiều |
| **2 đường routing song song**: ns-shim :8081 (Codex CLI thường, giữ nguyên) + codex-relay :4444 (chỉ agent của pipeline, env cô lập) | Không đụng vào stack đang chạy ổn định |
| **Docker chuyển thành KHÔNG bắt buộc** | Không còn RAGFlow → không thành phần nào cần Docker. Vẫn nên cài để tiện sau này |
| **pptx-tools, html-to-pptx vẫn giữ** | Tài nguyên nhẹ, không xung đột với quyết định trên |

**RAGFlow mục 5 và các yêu cầu Docker dưới đây chỉ áp dụng nếu sau này quay lại dùng RAGFlow.**

---

## 1. Yêu cầu phần cứng

| Thành phần | Tối thiểu | Khuyến nghị | Nguồn |
|---|---|---|---|
| **CPU** | 4 cores (x86) | 8+ cores | ✅ Máy hiện có 8 cores |
| **RAM** | 14 GB (nới từ 16 GB vì bỏ RAGFlow) | 32 GB | ⚠️ Máy hiện có 14 GB — chạy Qdrant embedded mode |
| **Disk** | 50 GB trống | 80 GB SSD | ✅ Máy hiện có ~420 GB trống |
| **GPU** | Không bắt buộc | NVIDIA GPU | RAGFlow (nếu dùng) hỗ trợ cả CPU và GPU |

---

## 2. Yêu cầu phần mềm

| Phần mềm | Phiên bản tối thiểu | Ghi chú |
|---|---|---|
| **Python** | 3.11 – 3.13 | Unstructured hỗ trợ 3.11–3.13. ⚠️ Máy hiện có 3.14 — **cần venv riêng với 3.12** (cài bằng `uv`/pyenv/apt) |
| **Node.js** | 22+ | Codex CLI và html-to-pptx yêu cầu Node.js 22+. ✅ Máy hiện có v24 |
| **.NET SDK** | 10 | pptx-tools yêu cầu .NET 10 SDK. ❌ Máy chưa cài — cài theo mục 4 |
| **Docker** | 24.0.0 | ⚠️ **KHÔNG bắt buộc** sau khi bỏ RAGFlow (mục 0). Script vẫn check để báo trạng thái |
| **Google Chrome/Chromium** | Bất kỳ | html-to-pptx cần Chrome/Chromium để render DOM — hoặc `python -m playwright install chromium` (Giai đoạn 6) |
| **Git** | Bất kỳ | Clone repos |
| **Rust compiler** | Bất kỳ | Unstructured cần Rust để build transformers |

---

## 3. Script kiểm tra tự động

Tạo file `scripts/check_prerequisites.sh`:

```bash
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
```

Chạy:

```bash
chmod +x scripts/check_prerequisites.sh
./scripts/check_prerequisites.sh
```

---

## 4. Cài đặt phần mềm còn thiếu

### Python 3.11+
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip

# macOS
brew install python@3.11
```

### Node.js 22+
```bash
# Ubuntu (via nvm)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
nvm install 22 && nvm use 22
```

### .NET 10 SDK
```bash
# Ubuntu
wget https://dot.net/v1/dotnet-install.sh -O dotnet-install.sh
chmod +x dotnet-install.sh
./dotnet-install.sh --channel 10.0

# macOS
brew install dotnet@10
```

### Docker + Docker Compose
```bash
# Ubuntu
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
```

---

## 5. Ghi chú quan trọng

- **RAGFlow đã bị loại khỏi pipeline** (quyết định mục 0) — nếu sau này muốn dùng lại, cần **≥ 16 GB RAM + Docker ≥ 24.0.0 + 80 GB disk**.
- **Qdrant embedded mode** lưu index vào `data_pipeline/data/qdrant/` — không cần service, không cần Docker.
- **pptx-tools** chưa publish lên NuGet — phải build từ source với **.NET 10 SDK**.
- **html-to-pptx** cần **Chrome/Chromium** hoặc **Playwright Chromium** để render DOM.
- **Python 3.14** nằm ngoài vùng hỗ trợ của Unstructured — tạo venv 3.12 trước khi `pip install -r requirements.txt`.
