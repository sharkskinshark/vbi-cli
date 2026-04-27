# Provider Adapters

This document defines the v1 provider adapter migration contract. No extractor should be copied from the internal workspace until it can satisfy this contract.

## Adapter Purpose

A provider adapter converts provider evidence into normalized VBI records. Evidence may come from official APIs, billing pages, local telemetry, policy documents, or manual snapshots.

Adapters are not UI renderers, not billing reconcilers, and not credential managers.

## Adapter Interface

Each adapter should provide these conceptual operations:

```text
detect() -> ProviderAvailability
read_cache() -> NormalizedRecord | None
sync(force: bool = false) -> SyncResult
explain() -> ProviderExplanation
```

The exact Python API can evolve, but every adapter must preserve these responsibilities.

## ProviderAvailability

Required fields:

- `record_id`
- `provider`
- `product`
- `installed`
- `auth_state`
- `evidence_paths`
- `blocked_reason`

Rules:

- `installed=false` is not an error.
- `auth_state` must not expose credential contents.
- `evidence_paths` must avoid personal absolute paths in public reports.

## NormalizedRecord

Required fields are defined in `docs/DATA_CONTRACT.md`:

- `record_id`
- `provider`
- `product`
- `source_type`
- `updated_at`
- `confidence`

Recommended usage fields:

- `usage_value`
- `quota_limit`
- `unit`
- `period`
- `observed_reset_at`
- `policy_reset`
- `estimated_reset_at`
- `evidence_source`
- `blocked_reason`

## SyncResult

Required fields:

- `record_id`
- `status`: `updated`, `fresh_cache`, `unavailable`, `failed`, or `skipped`
- `record`
- `message`
- `error_code`

Rules:

- `fresh_cache` means no provider API call was needed.
- `unavailable` means the adapter behaved correctly but evidence is unavailable.
- `failed` means adapter execution failed unexpectedly.

## Source Type Rules

| source_type | Adapter requirement |
| --- | --- |
| `official_api` | response comes from a validated provider endpoint |
| `billing_page` | evidence comes from a user-visible billing or usage page |
| `local_telemetry` | value comes from local logs, SQLite, JSONL, or app state |
| `policy_only` | only plan or policy limit is known, current usage is unavailable |
| `manual` | user-provided snapshot |
| `unavailable` | no usable evidence currently exists |

## Confidence Rules

| confidence | Requirement |
| --- | --- |
| `high` | direct provider evidence or strongly structured local source |
| `medium` | derived from local telemetry with stable schema |
| `low` | policy estimate, partial data, or indirect inference |
| `unknown` | insufficient evidence |

## Cache Freshness

Every adapter must define:

- cache file location under VBI-owned state
- freshness threshold
- whether stale cache can still be displayed
- whether sync can be skipped when fresh

Default behavior:

- `status` reads cache only
- `sync` skips fresh providers unless `--force` is explicit

## Reset Semantics

Adapters must keep reset fields separate:

- `observed_reset_at`: provider-observed timestamp
- `policy_reset`: policy rule such as `5h rolling window`
- `estimated_reset_at`: derived timestamp, presentation must mark as estimated

Never synthesize an official-looking reset line from `policy_reset` alone.

## Read-only Local Forensics

Local telemetry adapters must:

- open SQLite with read-only mode when possible
- avoid writing to provider-owned files
- avoid copying provider logs into the repo
- avoid printing credential-like fields
- sample large local stores before deep parsing

## Migration Tiers

| Tier | Meaning | Action |
| --- | --- | --- |
| A | safe to migrate after light cleanup | move into `vbi/providers/` with tests |
| B | useful but needs contract rewrite | refactor before migration |
| C | internal research only | keep in internal repo |
| D | unsafe for public release | remove or redesign before any migration |

## Initial Provider Assessment

| Provider | Current expectation | Tier | Notes |
| --- | --- | --- | --- |
| Claude | local telemetry plus provider-visible usage evidence when available | B | must separate observed reset from estimated reset |
| Codex | local telemetry first | B | telemetry is not official quota; source_type must stay honest |
| Gemini | official API only after endpoint is validated | D | hardcoded OAuth material must not migrate |
| Antigravity | local app state and Google One credits evidence | B | local forensics must remain read-only and privacy-safe |
| GitHub Copilot | host-managed quota unless official evidence is confirmed | C | do not invent usage when provider hides it |
| VS Code | inventory metadata, not quota | A | can migrate as metadata/provider availability adapter |

## Migration Gate

Before copying any extractor from the internal workspace, answer:

1. What evidence does it read?
2. Does it read in a provider-safe, read-only way?
3. Does it print or embed secrets, tokens, local usernames, or absolute paths?
4. What `source_type` does each output value use?
5. Does it write only VBI-owned cache?
6. How does it behave with missing provider, missing auth, empty data, and parser failure?
7. Does it require Windows-only behavior?
8. What tests prove the adapter contract?

## First Implementation Target

Completed first target: `scaffold/unavailable` adapter.

The scaffold proves:

- adapter registry exists
- `detect()` returns `ProviderAvailability`
- `sync()` returns `SyncResult`
- degraded provider behavior is explicit
- no local private data is read
- `status` can display `source_type: unavailable`

Next low-risk target: VS Code metadata adapter. It should detect metadata only and must not claim quota or usage.

