# Codex CLI + Bifrost + Agnes AI — dotfiles & tooling

Personal machine setup for routing **OpenAI Codex CLI** through a local
**Bifrost gateway** to **Agnes AI**, with a local compatibility shim.

```
Codex CLI ──> ns-shim :8081 ──> Bifrost :8080 ──> Agnes AI
              (flatten `namespace` tools,   (gateway: caching, routing,
               strip `reasoning` items)     logging, virtual keys)
```

## What's tracked here

| Path | Purpose |
|---|---|
| `tools/codex-ns-shim/shim.py` | Local proxy: flattens Codex `namespace` MCP tools into `function` tools and strips `reasoning` items that Agnes rejects with 400 |
| `tools/codex-ns-shim/start-shim.sh` | Shim launcher (`start/stop/status/restart`, setsid-detached so it survives the shell) |
| `tools/codex-ns-shim/test_shim.py` | Stdlib unit tests (12 tests, no pytest needed: `python3 test_shim.py`) |
| `tools/bifrost-stack.sh` | One-command stack manager: `up / status / down / logs` |
| `.codex/config.toml` | Codex CLI config (provider routing, MCP servers, token-saver settings) — reviewed, no credentials |
| `.codex/hooks.json` | squeez hook registration |
| `dotfiles/bashrc.example` | Template of `~/.bashrc` with the Virtual Key redacted |
| `dotfiles/bifrost-config.example.json` | Template of `~/.config/bifrost/config.json` with the setup token redacted |
| `docs/hethong.md` | Full system report (Vietnamese): architecture, incident log with fixes, token benchmark before/after enabling the TRL MCP — setup token redacted |

**Not tracked on purpose (contains secrets):** `~/.bashrc` (Bifrost Virtual
Key) and `~/.config/bifrost/config.json` (Bifrost setup token). Restore them
from the `dotfiles/*.example` templates and fill in your own values. The
tracked `docs/hethong.md` is the redacted copy; the original lives at
`~/hethong.md` with the real setup token and is git-ignored.

## Quick start

```bash
# 1. Start the stack (idempotent — safe to run again)
bifrost                # or: ~/tools/bifrost-stack.sh up

# 2. Check
bifrost status         # both [OK] = ready
curl http://localhost:8080/health

# 3. Use Codex
codex                          # interactive
codex exec "prompt"            # non-interactive (add --skip-git-repo-check outside a git repo)
codex -c 'model_reasoning_effort="high"' "hard task"
```

`bifrost` is a shell function defined in `~/.bashrc` (see
`dotfiles/bashrc.example`). Subcommands: `bifrost status`, `bifrost down`,
`bifrost logs`.

## Restore on a new machine

Repo: `vipera-iso/dotfiles` (private).

```bash
git clone https://github.com/vipera-iso/dotfiles ~            # tracks files at their real paths under $HOME
cp dotfiles/bashrc.example ~/.bashrc                    # then paste YOUR Virtual Key
mkdir -p ~/.config/bifrost
cp dotfiles/bifrost-config.example.json ~/.config/bifrost/config.json   # then paste YOUR setup token
bifrost
```

## Token hygiene

- Never paste PATs into chat or shell history. Prefer `gh auth login` or a
  credential helper; when a one-off authenticated push is unavoidable, use an
  inline URL and revoke the token right after.

## Shim behavior details

- **Namespace flattening** — Codex (openai/codex#23186) wraps MCP tools as
  `{"type":"namespace","name":"mcp__<server>__","tools":[...]}`; strict
  OpenAI-compatible upstreams reject it. The shim flattens each wrapper into
  top-level `function` tools, keeping `mcp__<server>__<tool>` names so tool
  calls route back to the right MCP server.
- **Reasoning stripping** — Agnes's Responses deserializer has no
  `reasoning` variant in its `input` enum, and Codex re-sends prior-turn
  reasoning items after each tool call (400 otherwise). The shim removes
  them. Kill-switch: `SHIM_KEEP_REASONING=1`.
- **Debug request dump** — start the shim with `SHIM_DUMP=/tmp/shim-dumps`
  to write every request body to disk.
- **Stats/health** — `curl -X HEALTH http://127.0.0.1:8081/` returns request,
  flattened and reasoning_stripped counters.

## Tests

```bash
python3 tools/codex-ns-shim/test_shim.py    # 12 tests, stdlib only
```
