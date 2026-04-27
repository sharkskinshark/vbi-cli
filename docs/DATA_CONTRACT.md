# Data Contract

## Normalized Provider Record

Required fields:

- `record_id`
- `provider`
- `product`
- `source_type`
- `updated_at`
- `confidence`

Recommended fields:

- `usage_value`
- `quota_limit`
- `unit`
- `period`
- `session_count`
- `observed_reset_at`
- `policy_reset`
- `estimated_reset_at`
- `cost_value`
- `cost_currency`
- `cost_period`
- `evidence_source`
- `blocked_reason`

## Cost Fields

`cost_value`, `cost_currency`, and `cost_period` describe the spend the adapter has observed for the record. They are optional. Adapters should populate them only when a defensible source exists (provider billing API, local telemetry plus published pricing, manual entry).

| Field | Meaning |
| --- | --- |
| `cost_value` | numeric cost incurred during `cost_period`; same currency as `cost_currency` |
| `cost_currency` | ISO 4217 code such as `USD`, `EUR`, `JPY`; default rendering assumes `USD` |
| `cost_period` | descriptor of the period the cost covers; common values: `today`, `session`, `billing_cycle`, `month`, `since_start` |

Cost rendering rules:

- `cost_value` must not be presented as official billing unless `source_type` is `official_api` or `billing_page`.
- Adapters that derive cost from local telemetry plus pricing tables must keep `source_type` aligned (typically `local_telemetry`) and use `evidence_source` to cite the pricing table (for example `local_telemetry+litellm_pricing`).
- A cost value without a `cost_period` should be rendered as a point-in-time snapshot, not a rate.

## Source Types

| source_type | Meaning |
| --- | --- |
| `official_api` | provider API returned usage or quota evidence |
| `billing_page` | user-visible billing or usage page evidence |
| `local_telemetry` | local logs, SQLite, JSONL, or app state evidence |
| `policy_only` | provider policy exists but current usage is unavailable |
| `manual` | user-entered snapshot |
| `unavailable` | no usable evidence currently available |

## Confidence

| confidence | Meaning |
| --- | --- |
| `high` | direct provider or strongly structured local source |
| `medium` | derived from local telemetry or consistent cache |
| `low` | estimate, policy mapping, or incomplete evidence |
| `unknown` | insufficient evidence |

## Reset Rules

Use `observed_reset_at` only when provider-observed evidence supplies a timestamp. Use `policy_reset` for plan rules. Use `estimated_reset_at` for derived timestamps and mark them as estimated in presentation.
