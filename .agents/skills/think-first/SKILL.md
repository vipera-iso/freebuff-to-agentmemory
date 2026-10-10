---
name: think-first
description: "Use on EVERY non-trivial turn, before answering - bugs, root cause, design, trade-offs, architecture, performance, security, migrations, reviews, plans, estimates. Calls the thinking MCP protocol (think -> think_submit) without the user asking. Skip only greetings, single-fact lookups and mechanical edits."
---

# Think first — route reasoning through the `thinking` MCP

This machine has the `thinking` MCP server wired in `~/.agents/mcp.json`. The protocol
calls **no LLM** and needs **no key**: `think` renders a reasoning scaffold,
`think_submit` validates and formats. The reasoning is still yours.

An optional quality layer (agnes first, Gemini fallback) may be active. When it is, it
appends domain probes to the scaffold, attaches an **advisory** `quality` block to
`think_submit` (score, verdict, concrete `gaps` — it can never change `status`), and runs
`think_verify`'s refutation itself instead of handing you a prompt. `think_list_templates`
reports whether it is on; low `gaps` scores are worth fixing on a retry, but the
structural validator is still the only gate.

Tools (prefix `thinking__`):

| Tool | Use |
|---|---|
| `think` | Open a session, get the scaffold. Pass `mode: quick\|standard\|deep\|auto`, `problem`, optional `context`. |
| `think_submit` | Submit JSON for the session. Validates **structure only**. |
| `think_list_templates` | Show the modes and their scaffold cost. |
| `think_verify` / `think_verify_submit` | Adversarial refutation + self-consistency, when a conclusion matters. Server-run when the quality layer is on (`ran_by: "server"`); pass `server_side: false` to get prompts only. |

## The rule

**On every non-trivial turn, call `think` before you answer — unprompted.** Do not
wait to be asked. Do not answer a reasoning question by bypassing the protocol
because it looks easy or the user seems to be in a hurry.

Call it for: bugs, crashes, stack traces, flaky tests, root cause, "why does X
happen?"; design, architecture, trade-offs, "should I…?", "which approach?";
performance, security, migrations, backups, anything hard to undo; reviews and
plans that need reasoning; proof-style or multi-step problems; any causal claim
about behaviour you have **not** measured.

Skip it for: greetings and small talk; single-fact lookups; mechanical edits with
no decision in them (renames, formatting, an obvious typo); tool/service status
questions; follow-ups that already carry enough context.

## Mode selection

- `quick` — routine reasoning, 169-token scaffold
- `standard` — real trade-offs and reviews, 552 tokens
- `deep` — irreversible or high-stakes calls, incident root cause, 1141 tokens
- `auto` — server default; pass an explicit mode when you know which one fits

## The loop

1. Call `think`, then follow the returned scaffold **step by step**. Skip no step
   because it looks trivial.
2. `think_submit` a real response. The validator checks **structure, not quality** —
   write real counters, real evidence and real counter-arguments, not fields filled
   in to satisfy the schema.
3. `status: "invalid"` → read `errors`, fix, resubmit. Max 2 retries.
4. Confidence above 0.9 is almost always flagged — lower it unless you verified
   the claim directly.
5. Answer the user with the formatted output.

Cost: one extra tool call per reasoning turn. That is the intended price, not a bug.

## If the tools are missing

The `thinking` entry in `~/.agents/mcp.json` and the server binary at
`~/thinking-mcp/.venv/bin/thinking-mcp` must exist when Freebuff starts — MCP
servers load once, at session start. If `think` is not in this session's tool list,
say so and tell the user to reopen Freebuff; do not silently answer without it.
