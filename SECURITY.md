# Security Policy

## Supported versions

SentinelMCP is pre-alpha and has no supported production release. Security fixes apply to the
latest commit on `main` until the first release line is established.

## Reporting a vulnerability

Do not open a public issue containing exploit details, credentials, personal data, or other
sensitive material. Use GitHub's private vulnerability reporting feature for this repository.
If that feature is unavailable, open a minimal public issue asking the maintainer to provide a
private reporting channel without disclosing the vulnerability.

Include affected versions, prerequisites, impact, reproduction steps, and suggested
mitigations when possible. Never include real employer systems or proprietary data in a
report.

## Development data policy

- Use only synthetic services and fixtures.
- Never commit credentials, access tokens, production traces, or real tool-call payloads.
- Treat audit, trace, and replay artifacts as potentially sensitive even in local development.
- Fault injection must remain disabled outside explicitly configured test environments.

The design-level security analysis is in `docs/THREAT_MODEL.md`.
