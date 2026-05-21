# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| Latest minor (e.g. 1.4.x) | Security + bug fixes |
| Previous minor (e.g. 1.3.x) | Security + bug fixes |
| Older | Security fixes only for 90 days after supersession |
| Pre-1.0 (0.x) | Latest release only |

## Reporting a vulnerability

Please report suspected vulnerabilities privately via
[GitHub Security Advisories](https://github.com/jjviscomi/bqemulator/security/advisories/new).

Do **not** open a public issue for a suspected vulnerability.

We follow a 90-day coordinated disclosure policy:

1. We acknowledge reports within 3 business days.
2. We investigate and confirm within 14 days.
3. We target a fix within 60 days of confirmation.
4. We publish an advisory and release the fix by day 90 at the latest.

Longer windows may be negotiated for complex issues.

## Scope

This project is a local emulator intended for development and testing. It
does **not** verify authentication and accepts any credentials. That is by
design for the emulator's use case; it is not a vulnerability in the
emulator itself.

Vulnerabilities in scope include:

- Remote code execution via crafted SQL or proto payloads.
- Sandbox escapes in the JavaScript UDF runtime.
- Path traversal in load / extract job file handling.
- Denial of service via unbounded resource consumption.
- Supply-chain concerns in our published artifacts.

Out of scope:

- Lack of authentication enforcement (by design — use in local development
  environments only).
- Issues in third-party dependencies not reachable from emulator code.

## Release signing

- Container images published to `ghcr.io/jjviscomi/bqemulator` are signed
  with [cosign](https://github.com/sigstore/cosign) and include SLSA
  provenance attestations.
- Wheels published to PyPI include [sigstore](https://sigstore.dev)
  attestations via Trusted Publishing.

Verification instructions live in
[docs/architecture/contributing/release-process.md](docs/architecture/contributing/release-process.md).
