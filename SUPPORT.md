# Support

`bqemulator` is an open-source project maintained by volunteers. The
fastest way to get help is to pick the right channel for your question.

## I have a question

Open a **[GitHub Discussion](https://github.com/jjviscomi/bqemulator/discussions)**:

- *How do I do X with the emulator?*
- *Is behavior Y expected, or is it a bug?*
- *Best practice for integrating with framework Z?*
- *Has anyone else solved …?*

Before posting, please:

1. Check the [documentation](https://jjviscomi.github.io/bqemulator/) —
   especially the
   [guides](https://jjviscomi.github.io/bqemulator/guides/loading-data/)
   and
   [troubleshooting reference](https://jjviscomi.github.io/bqemulator/reference/troubleshooting/).
2. Skim the
   [compatibility matrix](https://jjviscomi.github.io/bqemulator/reference/compatibility-matrix/)
   and
   [out-of-scope catalogue](https://jjviscomi.github.io/bqemulator/reference/out-of-scope/) —
   sometimes "this doesn't work" is "this isn't supported."
3. Search existing [Discussions](https://github.com/jjviscomi/bqemulator/discussions)
   and [Issues](https://github.com/jjviscomi/bqemulator/issues)
   for the same question.

## I found a bug

Open a **[GitHub Issue](https://github.com/jjviscomi/bqemulator/issues/new/choose)**
using the bug-report template. Please include:

- The emulator version (`bqemulator --version` or the Docker image
  tag).
- A minimal reproduction (a few lines of code or a query that
  triggers the bug).
- What you expected vs. what happened.
- Relevant environment info — OS, Python version, client library
  version.

If you can verify the same input behaves correctly against real
BigQuery, please say so — that turns a vague report into an
actionable conformance gap.

## I want to request a feature

Open a **[GitHub Issue](https://github.com/jjviscomi/bqemulator/issues/new/choose)**
with the feature-request template. For substantial changes (new SQL
features, new API surfaces, persistence-format changes), open an
[RFC](https://github.com/jjviscomi/bqemulator/tree/main/docs/rfcs)
*before* opening a PR.

Check [`docs/reference/out-of-scope.md`](docs/reference/out-of-scope.md)
first — we have a documented no-deferral policy, and explicit
out-of-scope items will not be reconsidered without an RFC.

## I found a security vulnerability

Please **do not open a public issue**. Report privately via
[GitHub Security Advisories](https://github.com/jjviscomi/bqemulator/security/advisories/new).
See [`SECURITY.md`](SECURITY.md) for the full disclosure policy and
SLAs.

## I want to contribute

Read [`CONTRIBUTING.md`](CONTRIBUTING.md), then check out:

- [`good first issue`](https://github.com/jjviscomi/bqemulator/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
  tagged issues for entry points.
- The
  [conformance coverage matrix](https://jjviscomi.github.io/bqemulator/reference/conformance-coverage-matrix/)
  — items marked 🔴 / 🟡 are well-scoped fixture-authoring tasks.
- [`docs/architecture/contributing/`](docs/architecture/contributing/)
  for SQL function additions, UDF runtime additions, conformance
  cases, and debugging.

## Response expectations

This is a small open-source project. We aim to respond to issues and
Discussions within **5 business days**, but cannot guarantee it. PRs
that ship with tests, an ADR (if required), and a CHANGELOG entry are
the fastest path to merge.

If you need a guaranteed response or commercial support, contact the
maintainers via their public GitHub profiles.
