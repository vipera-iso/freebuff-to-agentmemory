# BÁO CÁO TỔNG HỢP HỆ THỐNG — Codex CLI + Bifrost + Agnes AI

> Cập nhật: 2026-09-19 · Máy: Linux x86_64 (Ubuntu 26.4) · Node v24.21.0 · Python 3.14

---

## 1. Kiến trúc tổng quan

```
┌──────────────┐  Responses API  ┌───────────────┐  Responses API  ┌──────────────────┐  OpenAI protocol  ┌──────────────────┐
│  Codex CLI   │ ──────────────► │  ns-shim      │ ──────────────► │  Bifrost Gateway  │ ────────────────► │     Agnes AI     │
│  v0.155.1    │  localhost:8081 │  (flatten     │  localhost:8080 │  v2.2.1 (:8080)   │  apihub.agnes-ai  │  agnes-2.5-flash │
└──────────────┘                 │   namespace)  │                 └──────────────────┘                   └──────────────────┘
                                 └───────────────┘
      │
      ├── MCP servers:  trl-retrieve (AST retrieval) · context7 (docs tra cứu)
      ├── Plugin:       superpowers v6.4.1
      ├── Skills:       grill-me · create-implementation-plan · handoff · diagnosing-bugs
      ├── Hooks:        squeez v1.48.9 (nén output, persona off)
      └── AGENTS.md:    token-diet (behavior always-on)
```

**Luồng request:** Codex gửi POST `/openai/v1/responses` → **shim (8081)** làm phẳng tool type `namespace` thành `function` → Bifrost (8080) ghi log, áp dụng caching/routing → chuyển tiếp tới Agnes (`https://apihub.agnes-ai.com`) theo giao thức OpenAI → response stream quay lại Codex.

---

## 2. Thành phần & phiên bản (đã verify)

| Thành phần | Phiên bản | Trạng thái | Vị trí |
|---|---|---|---|
| Codex CLI | 0.155.1 (npm) | ✅ Hoạt động | `~/.nvm/versions/node/v24.21.0/bin/codex` |
| Bifrost Gateway | v2.2.1 (npm global) | ✅ Running, health ok (PID 26680) | `http://localhost:8080` |
| Provider `agnes` trong Bifrost | custom (base: openai) | ✅ active | base_url `https://apihub.agnes-ai.com` |
| codex-ns-shim | shim.py (Python, 12 tests) | ✅ flatten namespace + strip reasoning | `~/tools/codex-ns-shim/`, port 8081 |
| Agnes AI key | 1 key (`agnes-key-1`) | ✅ Đã verify, lưu masked | qua API Bifrost |
| squeez | 1.48.9 (npm global) | ✅ 3 hooks đăng ký, doctor ok | `~/.codex/hooks.json` |
| token-diet | (AGENTS.md block) | ✅ Always-on | `~/.codex/AGENTS.md` |
| TRL Token-Reduction | repo main + venv | ✅ MCP enabled (mcp 1.30.0 — PHẢI <2) | `~/tools/trl-token-reduction`, `~/tools/trl-venv` |
| Context7 MCP | v4.1.1 | ✅ enabled, handshake verified | npx `@upstash/context7-mcp` |
| Superpowers | 6.4.1 | ✅ installed, enabled | marketplace `superpowers-dev` |
| Skills (4) | — | ✅ On-demand | `~/.codex/skills/` |

---

## 3. Cấu hình Codex (`~/.codex/config.toml`)

```toml
model = "agnes/agnes-2.5-flash"
model_provider = "bifrost"

# Tương thích Agnes AI (không hỗ trợ tool type `namespace`/`web_search`)
web_search = "disabled"

[features]
multi_agent = false

[model_providers.bifrost]
name = "Bifrost"
base_url = "http://localhost:8080/openai/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
supports_websockets = false

# Combo 1: giảm token, không giảm độ thông minh
model_reasoning_effort = "low"          # nâng "medium"/"high" cho tác vụ khó
model_reasoning_summary = "none"
model_auto_compact_token_limit = 120000

[mcp_servers.trl-retrieve]
command = "/home/user/tools/trl-venv/bin/python"
args = ["-m", "plugin.mcp_server"]
[mcp_servers.trl-retrieve.env]
PYTHONPATH = "/home/user/tools/trl-token-reduction"
TRL_REPO = "."

[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[marketplaces.superpowers-dev]
source_type = "git"
source = "https://github.com/obra/superpowers.git"

[plugins."superpowers@superpowers-dev"]
enabled = true
```

**Cách chạy (TỰ ĐỘNG từ 2026-09-20 — systemd user units + linger):**
```bash
bifrost          # check/điều khiển: status | down | logs (qua ~/tools/bifrost-stack.sh)
# Cả 2 service tự start lúc bật máy (systemd --user + enable-linger),
# tự hồi sinh khi crash (Restart=always). Không cần bật tay nữa.

# Thủ công khi cần:
systemctl --user status bifrost codex-ns-shim
journalctl --user -u bifrost -n 20
```
> Function `bifrost` nằm trong `~/.bashrc` → gọi script `~/tools/bifrost-stack.sh`.
> Virtual Key đã tạo, `OPENAI_API_KEY` export sẵn trong `~/.bashrc` (key `sk-bf-…`).

Chạy Codex:
```bash
codex                    # interactive
codex exec "prompt"      # non-interactive
codex -c 'model_reasoning_effort="high"' "tác vụ khó"
```
> Lưu ý: chạy trong git repo, hoặc thêm `--skip-git-repo-check`.

---

## 4. Stack tiết kiệm token — mỗi tầng một tool

| Tầng | Tool | Cơ chế | Ghi chú |
|---|---|---|---|
| Gateway + caching | Bifrost | Auto prompt caching, routing, logging | Đứng một mình ở tầng gateway |
| Config Codex | 3 tham số | effort low · summary none · compact 120k | Tiết kiệm reasoning + nén context đúng lúc |
| Nén output terminal | squeez | Hooks PreToolUse/PostToolUse nén Bash output tới 95% | `persona = off` để không đụng tầng behavior |
| Behavior | token-diet | Directive always-on trong AGENTS.md; guardrail "concision never applies to reasoning" | ~31% hóa đơn (benchmark của tác giả) |
| Retrieval | TRL MCP | `retrieve_code`/`explain_symbol` trả AST slice thay vì đọc cả file (test thật: 920 tokens thay vì ~30k) | Không nằm trên request path — không xung đột |
| Docs tra cứu | Context7 | On-demand chèn docs mới nhất của thư viện | MCP schema luôn có trong context — đánh đổi đã chấp nhận |
| Workflow process | Superpowers | 15 skill quy trình + SessionStart hook | ⚠️ Trùng tầng behavior với token-diet (xem §7) |

**Đã loại bỏ có chủ đích:**
- `caveman` — trùng tầng behavior với token-diet (bản token-diet có guardrail + benchmark rõ hơn)
- `Firecrawl` — cần OAuth + thêm schema; chưa có nhu cầu
- `trl-proxy` — chỉ hỗ trợ `/v1/chat/completions`, không hỗ trợ `/v1/responses` của Codex → sẽ phá dây truyền
- `headroom` (npm) — placeholder rỗng v0.0.1, không phải tool thật

---

## 5. Skills đã cài (`~/.codex/skills/`)

| Skill | Nguồn | Dùng khi |
|---|---|---|
| `grill-me` | mattpocock/skills | Cần làm rõ yêu cầu trước khi code |
| `create-implementation-plan` | github/awesome-copilot | Lập kế hoạch thực thi có cấu trúc |
| `handoff` | mattpocock/skills | Nén phiên làm việc thành tài liệu chuyển tiếp |
| `diagnosing-bugs` | mattpocock/skills | Debug lỗi khó theo vòng lặp có hệ thống |

Cả 4 đều on-demand (`disable-model-invocation: true`) → không tốn context khi không gọi.
Backup đồng bộ tại `~/.agents/skills/`.

---

## 6. Lỗi tương thích đã gặp & cách fix

### 6.1. Tool type `namespace` bị Agnes từ chối
- **Triệu chứng:** `400 json_parse_error: tools[7].type: unknown variant 'namespace'` khi Codex gọi qua Bifrost → Agnes.
- **Chẩn đoán:** Dump payload bằng server cục bộ (`python3 /tmp/dump_server.py`) → tool[7] là nhóm `multi_agent_v1` (spawn_agent, wait_agent…), tool[11] là `web_search` — Agnes chỉ chấp nhận `function`, `web_search_preview`, `code_interpreter`, `mcp`.
- **Fix vĩnh viễn:** `features.multi_agent = false` + `web_search = "disabled"` trong config.toml.
- **Kết quả:** End-to-end OK 2 lần liên tiếp (`CODEX-BIFROST-AGNES-OK`, `PERMANENT-CONFIG-OK`).

### 6.2. Bifrost custom provider
- API tạo provider + key: `POST /api/providers` (provider) và `POST /api/providers/agnes/keys` (key — body phẳng, KHÔNG bọc trong `"keys": [...]`, nếu bọc sẽ báo "Key value must not be empty").
- `network_config.base_url` = `https://apihub.agnes-ai.com` (KHÔNG có `/v1` — Bifrost tự thêm path chuẩn `/v1/chat/completions`, `/v1/responses`).

### 6.3. Venv Python (Python 3.14 thiếu ensurepip)
- `python3 -m venv` lỗi thiếu pip → fix bằng `python3 -m venv --without-pip` + `get-pip.py` từ bootstrap.pypa.io.

### 6.4. Agnes từ chối item `reasoning` trong `input` (đã fix 2026-09-20)
- **Triệu chứng:** Sau khi Codex gọi MCP tool, request kế tiếp bị 400: `input: data did not match any variant of untagged enum ResponseInput` — ngay cả khi đã flatten namespace tools xong.
- **Chẩn đoán:** Dump request qua shim (`SHIM_DUMP`) + replay matrix: **item duy nhất Agnes từ chối là `type:"reasoning"`** trong `input` (Codex gửi lại reasoning items của turn trước khi gọi tool; bỏ nó → 200, cắt gọt field bên trong → vẫn 400).
- **Fix:** Shim strip các item `reasoning` khỏi `input` trước khi forward (kèm stats `reasoning_stripped`; kill-switch `SHIM_KEEP_REASONING=1`). Đánh đổi: model mất chain-of-thought nội bộ giữa các turn — không ảnh hưởng chất lượng đáng kể vì Codex tự giữ context qua messages.
- **Kết quả:** Verify end-to-end bằng MCP tool thật: `mcp__context7__query_docs` round-trip hoàn chỉnh (function_call → MCP → function_call_output), Bifrost log ghi `tool_call_names` + `status=success` toàn bộ, 0 lỗi 400.

### 6.5. Shim chết sau khi shell kết thúc (đã fix 2026-09-20)
- **Triệu chứng:** `codex` báo connection refused tới `localhost:8081`; shim chỉ chạy được đúng 1 request đầu rồi biến mất.
- **Nguyên nhân:** `start-shim.sh` chỉ dùng `nohup ... &` — shim nằm cùng process group với shell, bị giết khi session shell kết thúc. Bifrost không dính lỗi này vì đã chạy bằng `setsid`.
- **Fix:** thêm `setsid` vào `start-shim.sh` (đã sửa vĩnh viễn). Cả 2 service giờ đều phải khởi động bằng `setsid nohup`.
- **Bài học:** sau khi khởi động lại máy, PHẢI bật lại cả Bifrost lẫn shim (xem "Cách chạy" §3) — không có service nào tự start.

### 6.6. Service tự khởi động bằng systemd --user + linger (2026-09-20)
- **Vấn đề:** trước đây cả 2 service phải bật tay sau mỗi lần khởi động máy.
- **Fix:** 2 unit `bifrost.service` + `codex-ns-shim.service` trong `~/.config/systemd/user/`, `Restart=always` (shim thoát sạch bằng SIGTERM vẫn được hồi sinh — `on-failure` không đủ), `loginctl enable-linger user` để user manager chạy lúc boot mà không cần login.
- **Bẫy đã gặp:** instance manual cũ (setsid) giữ port 8080 khiến unit systemd crash-loop exit 1 — khi chuyển sang systemd phải kill tiến trình manual trước (tìm PID qua `ss -tlnp`).
- `bifrost-stack.sh` tự phát hiện unit có tồn tại hay không: dùng systemctl, không có thì fallback setsid nohup.

### 6.7. Venv TRL sai version MCP SDK → trl-retrieve chết (đã fix 2026-09-20)
- **Triệu chứng:** MCP server `trl-retrieve` chết ngay khi khởi động: `MCP startup failed: handshaking with MCP server failed: connection closed`. Codex fallback sang grep/thường (tốn token gấp nhiều lần).
- **Nguyên nhân:** Venv có `mcp 2.2.0` nhưng plugin dùng FastMCP API 1.x — docstring mcp 2 xác nhận `FastMCP` đã bị xoá (`Removed in mcp 2: FastMCP is now mcp.server.mcpserver.MCPServer`). Requirements chỉ ghi `mcp>=1.0` nên 2.2.0 lọt qua.
- **Fix:** `~/tools/trl-venv/bin/pip install "mcp>=1.16,<2"` → bản 1.30.0. Handshake OK, tools/list trả `retrieve_code` + `explain_symbol`.
- **Lưu ý:** repo TRL bị read-only nên không pin được trong requirements — nếu cài lại venv, PHẢI downgrade mcp <2.

### 6.7. Marketplace Superpowers
- Hướng dẫn trên mạng (`~/.codex/INSTALL.md`) đã lỗi thời (404). Cách đúng: repo có `.codex-plugin/` + `marketplace.json` → dùng chính thức `codex plugin marketplace add obra/superpowers` rồi `codex plugin add superpowers@superpowers-dev`.

---

## 7. Rủi ro đã ghi nhận (chưa xử lý)

| Rủi ro | Chi tiết | Xử lý đề xuất |
|---|---|---|
| ⚠️ **Gateway chưa có admin** | Dashboard/API `:8080` ai truy cập máy đều sửa được config/key | Mở UI → tạo admin bằng setup token (xem §8) |
| ⚠️ **Chưa có Virtual Key** | Codex đang dùng giá trị OPENAI_API_KEY bất kỳ (Bifrost không kiểm tra) | Tạo Virtual Key trong Bifrost, đổi env var theo |
| ⚠️ **Superpowers × token-diet** | Cùng tầng behavior, 2 directive always-on mỗi session | Quan sát 1–2 session; nếu loạn: gỡ 1 trong 2 |
| ⚠️ **Key Agnes đã lộ qua chat** | Key gửi dạng text trong hội thoại | Rotate key trên platform Agnes khi cần |
| ℹ️ Model metadata warning | Codex không có metadata `agnes/*` trong catalogue → dùng fallback (context window thô) | Có thể tạo `model_catalog_json` theo docs Bifrost nếu cần |

---

## 8. Thông tin vận hành nhanh

**Setup token Bifrost** (đã lưu trong `~/.config/bifrost/config.json`):
```
<REDACTED>  # xem file thật: ~/.config/bifrost/config.json (không commit)
```

**Quản lý Bifrost:**
```bash
curl http://localhost:8080/health                 # kiểm tra sống
tail -f /tmp/bifrost.log                          # log
pkill -f "bin/bifrost"                            # dừng (cẩn thận khớp đúng tiến trình)
setsid nohup bifrost > /tmp/bifrost.log 2>&1 &    # chạy nền detached
```

**Quản lý Codex:**
```bash
codex mcp list          # xem MCP servers
codex plugin list       # xem plugins
codex doctor            # chẩn đoán tổng thể
squeez doctor           # chẩn đoán hooks squeez
```

**Bảng giá/log requests:** dashboard `http://localhost:8080` (đo baseline token trước/sau khi thêm layer mới — nguyên tắc: đo trước, cài sau).

### 8.1. Benchmark token: TRL MCP hoạt động vs không (2026-09-20)

Đo bằng tổng `total_tokens` từ Bifrost log, cửa sổ thời gian quanh mỗi phiên:

| So sánh | requests | Tổng tokens | Kết luận |
|---|---|---|---|
| **BEFORE** — TRL chết (mcp 2.x), fallback grep toàn repo | 26 | **510,181** | 1 phiên trả lời câu hỏi đơn giản |
| **AFTER** — TRL sống (mcp 1.30.0), round-trip `retrieve_code` | 3 | **29,863** | Cùng dạng câu hỏi |

→ Sau fix: **~480k tokens ít hơn (~94%)** cho tác vụ tương đương. Con số 94% cao vì arm BEFORE bị nhân lên bởi nhiều vòng tool-call grep/tail do model đi vòng; với bench đối chứng chặt (cùng câu hỏi, chạy tuần tự, chỉ khác có/không TRL): **tiết kiệm ~6.8% (99,095 → 92,312)** — khi TRL hoạt động đúng, model dừng sớm hơn và ít đi vòng.

> ⚠️ Bài học benchmark: không phân loại arm theo `tool_call_names` của từng request (cửa sổ thời gian lệch sẽ nhầm arm); phải định biên theo thời gian bắt đầu/kết thúc mỗi phiên.

**Gỡ từng layer khi cần:**
```bash
# token-diet
curl -fsSL https://raw.githubusercontent.com/Kulaxyz/token-diet/main/install.sh | bash -s -- -a codex --uninstall
# squeez
squeez uninstall --host=codex
# Superpowers
codex plugin remove superpowers@superpowers-dev
# Context7
codex mcp remove context7
# TRL: xóa block [mcp_servers.trl-retrieve] trong config.toml
```

---

## 9. Kiểm thử đã thực hiện (bằng chứng)

| # | Test | Kết quả |
|---|---|---|
| 1 | Agnes trực tiếp `/v1/chat/completions` | ✅ 200, có usage |
| 2 | Agnes trực tiếp `/v1/responses` | ✅ 200 (hỗ trợ native) |
| 3 | Bifrost `/openai/v1/responses` → Agnes | ✅ 200, response đầy đủ |
| 4 | `codex exec` end-to-end (sau fix tools) | ✅ `CODEX-BIFROST-AGNES-OK` (~8.5k tokens) |
| 5 | `codex exec` với config vĩnh viễn | ✅ `PERMANENT-CONFIG-OK` |
| 6 | TRL retrieval (`plugin.cli`) | ✅ 3 slices / 920 tokens (index 94 files) |
| 7 | Context7 MCP handshake | ✅ server v4.1.1 |
| 8 | squeez doctor | ✅ tất cả [ok], persona off |
| 10 | TRL MCP round-trip sau fix (exec trong repo TRL) | ✅ `mcp__trl_retrieve__retrieve_code` gọi thật, Bifrost `status=success`, TRL-ROUNDTRIP-OK |
| 11 | Superpowers trong phiên | ✅ Block `<skills_instructions>` catalog 6.4.1 inject vào context mỗi phiên (SessionStart) |
| 12 | TUI interactive | ✅ TUI hoạt động (shim xử lý 86 requests); tự động hoá composer qua PTY không đáng tin — nên test bằng tay hoặc `codex exec` |
