# Security Policy

vbi-cli is local-first. It must not upload private usage data, tokens, OAuth credentials, local telemetry databases, or live cache files.

## Release Gate

Before publishing, run `vbi audit` and verify:

- no hardcoded secrets
- no personal email or local absolute paths
- no runtime cache artifacts
- no local SQLite or JSONL telemetry dumps
- no private OAuth material
- `.gitignore` includes required local-state and credential patterns
- tracked files do not include runtime or credential artifacts
- git history does not contain high-confidence token patterns
- local release trees do not contain `.google-mcp/`, `.claude/`, `.gcloud/`, or `accounts.json`

## Credential Handling

Provider adapters may detect credential presence, but must not print, export, copy, or commit credentials. The Codex adapter decodes the `id_token` JWT body for plan name + subscription expiry only; the signing key is never used and the token is never logged.
