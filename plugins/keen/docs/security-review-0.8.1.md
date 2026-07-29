# Keen 0.8.1 security review

**Reviewed:** July 28, 2026

**Scope:** The Keen 0.8.1 change set relative to the public 0.8 release

**Result:** Cleared for publication. No known open critical, high, or
medium-severity findings remain.

## What was reviewed

This review covered the browser capture boundary, authentication and final-page
verification, generated artifacts, historical-run compatibility, filesystem
output, reports, plugin instructions, packaging, dependencies, and installed
Claude Code and Codex bundles.

The review treated captured websites and existing `.keen/` directories as
untrusted input. It included pure-Python adversarial tests, browser-level
regressions, independent code-review passes, clean-wheel installation, and
readback from the installed plugin caches.

## Resolved findings

### Evidence integrity and honest coverage

- A capture manifest is now authoritative for the exact requested matrix.
  Downstream stages cannot silently promote stale DOM files or analysis from a
  different capture.
- Successful and diagnostic entries are bound to their screenshot, viewport,
  state, and expectation reason.
- Typed, bounded readers reject malformed geometry, identity fields, scores,
  and oversized artifacts before they enter analysis or reports.
- A capture-in-progress marker prevents interrupted current runs from being
  mistaken for compatible manifest-less historical evidence.
- Blank, canvas-only, WebGL, truncated, and partially observable surfaces now
  produce explicit incomplete or manual-review coverage instead of an
  overconfident score.

### Browser and local-development boundaries

- `--allow-internal` is scoped to explicitly named origins. A reviewed
  localhost page cannot read or frame an unrelated local service merely
  because internal capture was enabled.
- Separate development APIs require an exact, repeatable `--allow-origin`.
  Public subresources remain available.
- `--allow-file` authorizes exact target and auth documents. Same-directory
  publication assets are type-bounded, while sibling documents, traversal,
  symlink escapes, ambiguous remote file URLs, and unrelated local files are
  blocked.
- Redirects, frames, subresources, final URLs, and required selectors are
  rechecked at the evidence boundary. Diagnostic screenshots are never scored.
- WebSocket attempts remain blocked and are surfaced as provisional coverage.

### Secrets and untrusted page content

- Page URLs and element links are stripped of URL userinfo, queries, and
  fragments before they reach DOM, component, capture metadata, or report
  artifacts.
- Authentication failures and persisted URLs are redacted so signed callbacks,
  tokens, and credentials do not appear in logs or review output.
- Captured text, titles, accessibility names, URLs, screenshots, and report
  fields are explicitly treated as untrusted evidence in the agent contract;
  they cannot authorize tools, file changes, network access, or credential use.

### Filesystem output

- Core writers use atomic, symlink-safe output helpers.
- Repository-controlled symlinks, junctions, traversal, unsafe ancestors, and
  non-regular artifact paths fail closed.
- Partial writes cannot replace a previously valid artifact.

## Correctness findings resolved

- Zero-width borders no longer generate contrast findings for colors that are
  never rendered.
- Intentional disabled-control opacity is evaluated as a disabled state rather
  than an overlay mismatch.
- Visibility and disabled-state checks account for composed/shadow ancestry and
  disabled fieldsets.
- Accessibility-name coverage is capped and reported honestly.
- Legacy 0.8 DOM null-style values and manifest semantics remain readable
  without weakening validation for current runs.

## Verification

The release candidate passed:

- 1,192 automated tests, with two platform/environment-specific skips
- browser regressions for internal-origin isolation, local-file document
  isolation, interrupted captures, final-state checks, and credential removal
- Ruff lint and formatting checks
- Mypy across the runtime package
- prompt validation and 12 static skill-contract cases
- two direction-workshop evaluation cases across all four arms
- clean wheel build, isolated installation, runtime-asset checks, CLI doctor,
  and HTML-report smoke testing
- strict locked-dependency audit with no known vulnerabilities
- Bandit with no actionable findings
- Claude marketplace, Claude plugin, Codex plugin, and skill validation
- source/generated/installed hash equality for the Claude Code and Codex
  release surfaces
- successful loading of six historical real-world Keen runs

## Residual risk and user responsibility

Keen deliberately captures rendered pixels and structured page evidence.
Screenshots and reports can therefore contain private product content even when
URL credentials are removed. Keep disposable `.keen/` runs out of source
control and share them only with authorized people.

An explicitly allowed origin or local publication directory is part of the
user-approved review surface. Use the narrowest possible origin list, prefer a
small development server over reviewing a source directory containing private
files, and use only authentication instructions whose exact contents have been
reviewed.

The `--unsafe-auth-script` escape hatch executes arbitrary local Python by
design and remains outside the declarative-auth safety boundary.

This was a source, dependency, packaging, and adversarial browser review. It
was not an independent penetration test of every browser or operating-system
configuration.
