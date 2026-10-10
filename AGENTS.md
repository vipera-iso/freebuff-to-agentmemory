# AGENTS.md — Freebuff standing instructions

> Project-level standing instructions for the agent harnesses used on this machine.
> Freebuff discovers `AGENTS.md` / `CLAUDE.md` in the project tree as "knowledge
> files"; OpenCode and Codex read `AGENTS.md` natively. The Freebuff-guaranteed
> twin of this file is the `think-first` skill in `.agents/skills/` — its
> description reaches every session even when this file is not rendered.
>
> Keep it short: if it is injected, it is paid for in tokens on every session.
> It only loads while it stays un-ignored in the whitelist `.gitignore`.

## Think first: the `thinking` MCP on every reasoning turn

The `thinking` server is wired in `~/.agents/mcp.json`. Tools:
`thinking__think`, `thinking__think_submit`, `thinking__think_list_templates`
(plus `thinking__think_status` / `thinking__think_verify*` when useful).

**On every non-trivial turn, call `thinking__think` before you answer — unprompted,
without waiting to be asked.** Read the scaffold it returns, reason through it, then
close the loop with `thinking__think_submit`. Do not answer a reasoning question by
bypassing the protocol because it looks easy or the user seems in a hurry.

### Call `think` first for

- Bugs, crashes, stack traces, flaky tests, root cause, "why does X happen?"
- Design, architecture, trade-offs, "should I…?", "which approach is better?"
- Performance, security, migrations, backups — anything hard to undo
- Reviews and plans that need reasoning; proof-style or multi-step problems
- Any causal claim about behaviour you have not actually measured

### Answer directly (no `think`) for

- Greetings, small talk, single-fact lookups
- Mechanical edits with no decision in them: renames, formatting, an obvious typo
- Tool/service status questions, and follow-ups that already carry enough context

### Pick the mode

- `quick` — routine reasoning (169-token scaffold)
- `standard` — real trade-offs, reviews (552)
- `deep` — irreversible or high-stakes decisions, incident root cause (1141)
- `auto` is the server default; pass an explicit mode when you know which fits.

### The loop

1. Follow the returned scaffold step by step. Skip no step because it looks trivial.
2. `think_submit` a real response. The validator checks **structure only** — write
   real counters, real evidence and real counter-arguments, not schema-filling.
3. `status: "invalid"` → read `errors`, fix, resubmit. Max 2 retries.
4. Confidence above 0.9 is almost always flagged — lower it unless you verified directly.
5. Answer the user with the formatted output.
6. The protocol calls no LLM: the reasoning is yours, it structures thinking, it does not
   replace it. An optional quality layer (agnes first, Gemini as fallback, keys in
   `~/.agentmemory/.env`) may additionally append domain probes to the scaffold, attach
   an **advisory** `quality` audit to `think_submit`, and run `think_verify`'s refutation
   itself. Advisory means exactly that: `quality` never changes `status`, so treat its
   `gaps` as input, not as a verdict. `think_list_templates` reports whether it is active;
   `THINKING_LLM=0` turns it off.

Cost: one extra tool call per reasoning turn. That is the intended price, not a bug.

## Repo hygiene (this whitelist-gitignore repo)

- Everything is git-ignored by default: a new tracked file needs its own `!/path` line.
- Never commit secrets — real keys live outside git (`~/.agentmemory/.env`); only
  redacted `dotfiles/*.example` templates are tracked.
- Run the affected tests (typecheck / pytest) before claiming anything works.
