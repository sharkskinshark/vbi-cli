# vbi-cli

Local-first terminal dashboard for AI tool usage. Reads on-disk telemetry that the AI CLIs already write — no credentials read, no provider API calls.

## Install

Requires Python 3.10+.

Windows / PowerShell:

```powershell
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Typical Windows / PowerShell install time is about 25 seconds on this release build. Network speed, pip cache state, and antivirus scanning can move that number.

Developer install:

```bash
pip install -e .
```

## First Run

```bash
vbi live
```

The installer drops you into the interactive `vbi>` prompt. Useful first commands:

```text
live
status
inventory
map
audit
exit
```

You can also run commands directly:

```bash
vbi live              # continuous, refresh every 10s (Ctrl+C to exit)
vbi live --once       # one-shot snapshot
vbi live --interval 30
vbi status            # cached records only
vbi inventory         # discover installed AI tooling
vbi map               # host-first tooling map
vbi audit             # GitHub release safety scan
vbi export            # write sanitized JSON report to ~
```

`vbi export` writes a JSON report that can be consumed by other CLI tools or downstream AI workflows for further analysis and automation.

Sample `vbi live` frame:

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

## Limits

- Gemini CLI does not expose local token/quota data, so `vbi` reports session activity only.
- Claude Code 5h / Week reset bars require running `/usage` inside Claude Code first.
- Cost values derived from local telemetry are estimates, not official billing.

## Privacy

- Read-only on local files only
- No credential files are opened (`.credentials.json`, `oauth_creds.json`, `auth.json` JWT body is decoded only for plan/expiry — signing key is not used)
- No transcript content, no message bodies, no prompts
- No provider API calls during telemetry collection (`vbi update` and the startup update hint may run `git fetch`)
- Cache lives at `~/.vbi/cache/` (user-scoped, never committed)

## Data Contract

Each adapter has a strict documented contract about what is automatic vs. trigger-required. The contract is enforced in code: adapters do not probe provider APIs to fill gaps, and missing data is surfaced honestly with a hint when a manual trigger is required.

## Developer Notes

Read [`docs/PROVIDER_ADAPTERS.md`](docs/PROVIDER_ADAPTERS.md) and [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md). Each provider in `vbi/providers/` is a 200–400 line file with a documented data-collection contract at the top.
