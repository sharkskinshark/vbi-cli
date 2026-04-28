# vbi-cli

Local-first terminal dashboard for AI tool usage. Reads on-disk telemetry that the AI CLIs already write — no credentials read, no provider API calls.

## Install

Requires Python 3.10+.

On Windows/PowerShell, the installer typically takes about 25 seconds on this
release build; network speed, pip cache state, and antivirus scanning can move
that number.

```bash
pip install -e .
```

## Run

```bash
vbi live              # continuous, refresh every 10s (Ctrl+C to exit)
vbi live --once       # snapshot
vbi live --interval 30
```

Sample frame:

```text
 CLAUDE CODE  ·  Claude Pro  ·  5h
 ──────────────────────────────────────────────────────────────
 Tokens                                  2.1M tokens
 Session                                 3 today
 Cost                                    $32.01     today
 Spark    ▄▃▄▁                █▃▄▅▄▃▄▁   78.5K tokens this hr
 5h      [██████████░░░░░░░░░░░░░░░░░░]  3h 16m left   resets 04/26 18:36
 Week    [███████████░░░░░░░░░░░░░░░░░]  4d 08h left   resets 05/01
```

## Supported providers

| Provider | What's shown | Trigger required |
| --- | --- | --- |
| **Antigravity** (Google AI Pro/Ultra) | Plan, AI credits, monthly subscription requests, hourly rate-limit, Month reset | None — extension auto-writes SQLite + cloudcode.log |
| **Claude Code** | Tokens today, cost (estimated), sessions, hourly spark, 5h + Week reset | 5h/Week reset bars require running `/usage` inside Claude Code |
| **Codex CLI** (ChatGPT Plus/Pro) | Context tokens vs window, plan, subscription expiry, 5h + Week reset, quota % | None — every API call writes `rate_limits` to session JSONL |
| **Gemini CLI** | Session count today | No quota data — Gemini CLI doesn't log token usage locally |

## How data is collected

Each adapter has a strict, documented contract about what's auto vs. trigger-required. See the docstring at the top of each file in `vbi/providers/`. The contract is enforced in code — adapters never probe external APIs to fill gaps. When data is genuinely missing, a hint note tells you which command to run.

## Privacy

- Read-only on local files only
- No credential files are opened (`.credentials.json`, `oauth_creds.json`, `auth.json` JWT body is decoded only for plan/expiry — signing key is not used)
- No transcript content, no message bodies, no prompts
- No provider API calls during telemetry collection (`vbi update` and the startup update hint may run `git fetch`)
- Cache lives at `~/.vbi/cache/` (user-scoped, never committed)

## Adding a provider

Read [`docs/PROVIDER_ADAPTERS.md`](docs/PROVIDER_ADAPTERS.md) and [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md). Each provider in `vbi/providers/` is a 200–400 line file with a documented data-collection contract at the top.

## Other commands

```bash
vbi inventory              # discover installed AI tooling (Tier 1 registry)
vbi inventory --heuristics # plus generic discovery via PATH/npm/pipx/registry
vbi dashboard              # cache-only dashboard (doesn't sync)
vbi sync                   # refresh cache for all providers
vbi status                 # show cached records only (no sync)
vbi audit                  # release safety scan
```
