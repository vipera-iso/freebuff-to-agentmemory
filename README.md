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

**Not tracked on purpose (contains secrets):** `~/.bashrc` (Bifrost Virtual
Key), `~/.config/bifrost/config.json` (Bifrost setup token) and
`~/.agentmemory/.env` (LLM/embedding keys). Restore them from
`dotfiles/*.example` and fill in your own values. The tracked `docs/hethong.md`
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
