# freebuff-to-agentmemory

Dotfiles, systemd user units and MCP plumbing for one machine: a **shared
long-term memory** for coding agents (Freebuff, OpenCode) backed by
[agentmemory](https://github.com/rohitg00/agentmemory), plus the local data
pipeline around it.

```
Freebuff (.agents/mcp.json)  ┐
                             ├─ Streamable HTTP ─▶ agentmemory-mcp-bridge :8765/mcp
OpenCode (opencode.json)     ┘                              │ stdio
                                                            ▼
                                             @agentmemory/mcp ── REST ──▶ agentmemory :3111
```

## What's tracked here

| Path | Purpose |
|---|---|
| `.config/systemd/user/agentmemory.service` | agentmemory backend (REST `:3111`, streams `:3112`, viewer `:3113`) via `tools/agentmemory-server.sh` |
| `.config/systemd/user/agentmemory-mcp-bridge.service` | stdio → Streamable HTTP bridge on `:8765/mcp`, bound to the backend with `Wants=`/`After=`/`PartOf=` |
| `.config/systemd/user/pptx-mcp-bridge.service` | pptx-tools MCP bridge on `:3001/mcp` for `data_pipeline` |
| `agentmemory-mcp-bridge/` | Python package implementing the bridge — schema-preserving proxy, `/health`, upstream watchdog, tests |
| `tools/agentmemory-server.sh` | npx launcher: resolves Node from any nvm version, pins the agentmemory version |
| `data_pipeline/` | Skeleton for raw-data RAG (docs, real modules, test scripts) |
| `docs/hethong.md` | Full system report (Vietnamese): architecture, incident log, token benchmark — setup token redacted |
| `dotfiles/*.example` | Templates of the files that hold secrets |
| `AGENTS.md` | Standing instructions: route every non-trivial turn through the `thinking` MCP |
| `.agents/skills/think-first/` | Freebuff skill that carries the same trigger into every session's skill list |

**Not tracked on purpose (contains secrets):** `~/.config/bifrost/config.json`
(Bifrost setup token — stack đã gỡ 10/2026) and `~/.agentmemory/.env`
(LLM/embedding keys). Restore them from `dotfiles/*.example` and fill in your
own values. `~/.bashrc` cũng bị ignore, nhưng **không** còn chứa key nào
(không export `OPENAI_API_KEY` — xem `dotfiles/bashrc.example`). The tracked `docs/hethong.md`
is the redacted copy; the original lives at `~/hethong.md`.

This repo uses a **whitelist `.gitignore`**: everything is ignored, only the
paths explicitly un-ignored above are tracked. Adding a file means adding a
`!/path` line — that is the review gate for secrets.

## Quick start

Units auto-start at boot (`loginctl enable-linger` is already on), and
`Restart=always` revives anything that crashes.

```bash
systemctl --user daemon-reload
systemctl --user enable --now agentmemory agentmemory-mcp-bridge pptx-mcp-bridge

# Readiness — expect "status": "ok" and the full tool count
curl -s localhost:8765/health          # bridge
curl -s localhost:3111/agentmemory/health
```

`/health` reports `ok` / `degraded` (upstream flaky, `error` is set) / `starting`
/ `down`, and answers `503` whenever the upstream is not connected — it no
longer claims `ok` after the stdio child has died.

## Cấu hình provider cho agentmemory (key miễn phí)

agentmemory đọc cấu hình từ `~/.agentmemory/.env` (quyền `0600`, **không** track).
Template không chứa key: `dotfiles/agentmemory-env.example`.

| Biến | Giá trị | Ý nghĩa |
|---|---|---|
| `OPENAI_API_KEY` | key agnes (`sk-…`) | bật provider LLM OpenAI-compatible |
| `OPENAI_BASE_URL` | `https://apihub.agnes-ai.com/v1` | trỏ về agnes (base có path → dùng thẳng làm route) |
| `OPENAI_MODEL` | `agnes-3.0-flash` | **bắt buộc** — thiếu sẽ rơi về default `gpt-5.6-luna` và 404 |
| `FALLBACK_PROVIDERS` | `gemini` | chuỗi dự phòng khi agnes lỗi/limit (chỉ nhận: `anthropic`, `gemini`, `openrouter`, `agent-sdk`, `minimax`, `openai`) |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | key gemini (`AQ.…`) / `gemini-3.8-flash` | provider dự phòng |
| `EMBEDDING_PROVIDER` | `local` | vector trên máy (`Xenova/all-MiniLM-L6-v2`, 384-dim) — **phải đặt tường minh**, nếu không `GEMINI_API_KEY` sẽ bị chọn làm embedding provider |
| `AGENTMEMORY_AUTO_COMPRESS` | `true` | nén observation bằng LLM |
| `AGENTMEMORY_REFLECT` | `true` | tự tổng hợp lesson định kỳ |
| `CONSOLIDATION_ENABLED` | `true` | pipeline hợp nhất 4 tầng |
| `GRAPH_EXTRACTION_ENABLED` | `true` | trích quan hệ đồ thị bằng LLM |
| `AGENTMEMORY_INJECT_CONTEXT` | `true` | chèn ký ức vào prompt phiên sau |

**Thứ tự ưu tiên env:** `~/.agentmemory/.env` < `process.env` — một `OPENAI_API_KEY`
export trong shell sẽ **đè** file, nên `~/.bashrc` không còn export key Bifrost cũ.

Xác minh:

```bash
npx -y @agentmemory/agentmemory@0.9.29 status   # Provider: ✓ llm, Embeddings: ✓
curl -s localhost:8765/health                    # bridge: "tool_count": 54
```

**Tắt bớt nếu tốn token:** `CONSOLIDATION_ENABLED` và `AGENTMEMORY_AUTO_COMPRESS` tốn
LLM call nhất; tắt một trong hai rồi `systemctl --user restart agentmemory`.

**Luân chuyển key:** sửa giá trị trong `~/.agentmemory/.env` rồi
`systemctl --user restart agentmemory`.

## Tự động gọi thinking MCP mỗi lần suy luận

Freebuff **không có hooks** — MCP tool do model tự gọi, nên không có cách nào
cưỡng chế cứng. Cách duy nhất là *chỉ dẫn always-on*, và có hai kênh:

| Kênh | Được chèn khi nào | Trạng thái |
|---|---|---|
| `.agents/skills/think-first/SKILL.md` | `name` + `description` vào **danh sách skill** ở mọi phiên | **đã xác minh hiển thị** (danh sách skill có trong prompt phiên hiện tại) |
| `AGENTS.md` (gốc `$HOME`) | project knowledge file, nạp lúc mở phiên | chưa xác minh được từ trong phiên — Freebuff chỉ render block này nếu server chèn; **portable** (OpenCode/Codex đọc `AGENTS.md` trực tiếp) |

Cả hai trỏ về cùng một giao thức: `think` → theo scaffold → `think_submit`
(tối đa 2 retry khi `invalid`). Chi tiết trigger và cách chọn mode nằm trong hai
file đó.

**Ràng buộc dễ vỡ:** `AGENTS.md` **phải** được un-ignore trong `.gitignore`
(`!/AGENTS.md`). Freebuff dựng cây file bằng `TC()`, có áp ignore rules (`pX$`/`fXH`)
— file bị ignore sẽ không bao giờ trở thành knowledge file.

**Kiểm chứng (cần phiên mới — MCP + chỉ dẫn chỉ nạp lúc mở phiên):**

1. Mở lại Freebuff, hỏi một câu suy luận ("vì sao bridge exit khi upstream chết?")
2. Kỳ vọng thấy tool call `think` **trước** câu trả lời, rồi `think_submit`.

**Tắt:** xoá `~/.agents/skills/think-first/` (tắt ngay phiên sau) hoặc thêm
`disable-model-invocation: true` vào frontmatter. Thu hẹp xuống "mọi lượt, kể cả
chào hỏi" thì bỏ mục *Skip it for* trong SKILL.md.

## Thinking chất lượng: lớp LLM tuỳ chọn của thinking-mcp

Server gốc chỉ **ép cấu trúc** — nó không phân biệt được một counter thật với một counter
viết cho có. Lớp LLM tuỳ chọn trong `~/thinking-mcp/thinking_mcp/llm/` vá đúng chỗ đó và
**advisory** — không bao giờ làm fail một submission:

| Pass | Việc | Khi nào |
|---|---|---|
| audit | chấm `score` + liệt kê `gaps` cụ thể của submission | mỗi `think_submit` |
| probes | thêm check đặc thù cho đề bài vào cuối scaffold | mỗi `think` |
| verify | server tự chạy refutation (+ N sample) trong context riêng của nó | `think_verify` (`ran_by: "server"`) |

Key đọc thẳng từ `~/.agentmemory/.env` (0600, không track) — không nhân bản secret:

| Biến | Giá trị trên máy này |
|---|---|
| `THINKING_AGNES_KEYS` | pool 3 key agnes (free, không giới hạn) |
| `THINKING_GEMINI_KEYS` | pool 12 key gemini (giới hạn request) |

agnes chạy trước, gemini là fallback. Free tier giới hạn **theo từng key**, nên gặp
429/5xx thì key hiện tại bị loại và xoay sang key kế tiếp — đó là cách 12 key gemini bị
rate-limit trở thành một fallback dùng được. Hết sạch key → pass trả `unavailable` kèm lý
do, submission không bị ảnh hưởng. Không có key → layer **biến mất**, hành vi y như cũ ($0).

**Đo trên máy này** (agnes, free tier): probes ~7 s, audit ~9 s, refutation ~4 s (+ ~5 s
mỗi sample). Tắt từng pass: `THINKING_LLM_PROBES=0`; tắt hết: `THINKING_LLM=0` — sửa
`~/.agentmemory/.env` xong phải mở lại Freebuff, MCP chỉ nạp lúc mở phiên.

**Giới hạn giữ nguyên sự thật:** judge là *ý kiến*, có thể chấm sai một kết luận đúng. Có
lần nó tự mâu thuẫn (`score: 1.0` cùng `verdict: "hollow"`) nên server báo `inconsistent`
thay vì tự chọn một con số — chọn hộ là bịa ra một phép đo. `self_consistency` cần ≥2
sample: một mẫu không phải majority vote, nên server từ chối chạy một mẫu thay vì báo
`agreement: 1.0` từ một ý kiến.

## Logs

Everything goes to the journal (nothing is appended to `/tmp` anymore):

```bash
journalctl --user -u agentmemory -f
journalctl --user -u agentmemory-mcp-bridge -f
```

## How failures self-heal

| Failure | Behaviour |
|---|---|
| Backend down when the bridge starts | `ExecStartPre` waits up to 120s, then **fails** (`exit 1`) instead of starting blind into the 7/54-tool local fallback; `Restart=always` retries with backoff (3s → 60s), `StartLimitIntervalSec=0` so it never parks in `failed` |
| Backend restarts | `PartOf=agentmemory.service` restarts the bridge too |
| Upstream stdio process dies while the bridge lives | Watchdog (`list_tools()` every 30s, 3 consecutive failures) marks `degraded`, then exits cleanly so systemd rebuilds it with a fresh upstream |
| Node/nvm version changes | `tools/agentmemory-server.sh` globs `~/.nvm/versions/node/*/bin`, no hard-coded version |
| Crash-loop pumping in new code | agentmemory version is pinned — upgrade explicitly (below) |

Tune the watchdog with `AM_BRIDGE_PING_INTERVAL` (seconds, `0` disables),
`AM_BRIDGE_PING_TIMEOUT` and `AM_BRIDGE_PING_FAILURES`.

```bash
# Upgrade agentmemory
AGENTMEMORY_VERSION=x.y.z systemctl --user restart agentmemory
```

## Tests

```bash
cd agentmemory-mcp-bridge
uv venv .venv && uv pip install -e .
.venv/bin/python -m pytest -q          # bridge end-to-end + watchdog + config

data_pipeline/scripts/run_all_tests.sh # Test 1 → 11 (cần đủ .NET SDK/Docker/16GB RAM)
```

## Restore on a new machine

1. `git clone https://github.com/vipera-iso/freebuff-to-agentmemory.git ~/`
2. Copy the real (secret) files back from `dotfiles/*.example`
3. `uv venv ~/agentmemory-mcp-bridge/.venv && uv pip install -e .` inside it
4. `systemctl --user daemon-reload && systemctl --user enable --now agentmemory agentmemory-mcp-bridge pptx-mcp-bridge`
