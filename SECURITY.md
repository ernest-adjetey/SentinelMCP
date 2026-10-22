# Security Policy

## Supported versions

SentinelMCP 0.1.x is a public alpha and is not supported for production use. Security fixes apply
to the latest 0.1.x release and the latest commit on `main`.

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
