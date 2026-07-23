# Keen 0.8.0 security review

**Reviewed:** July 22, 2026

**Scope:** The complete Keen 0.8.0 change set relative to the public `main`
branch

**Result:** Cleared for publication. No known open critical, high, or
medium-severity findings remain.

## What was reviewed

The review covered the Python package, browser capture path, authentication
helpers, local direction workshop, generated HTML reports, bundled plugin
skills, dependencies, release packaging, and GitHub Actions.

Particular attention went to the places where Keen handles untrusted input:
remote URLs and redirects, rendered page data, authentication instructions,
temporary workshop files, user-supplied workshop choices, and generated HTML.

## Resolved findings

### SEC-001 — Vulnerable locked dependencies

**Severity before fix:** High

**Affected files:** `pyproject.toml`, `requirements.lock`, `uv.lock`

The pre-release lock contained Pillow 12.2.0 and pytest 8.4.2, which had
published security advisories. Image parsing is part of Keen's normal capture
workflow, so the Pillow finding was release-blocking.

**Fix:** Pillow now requires 12.3.0 or newer, pytest requires 9.0.3 or newer,
and pytest-asyncio requires 1.4 or newer. Both lock files were regenerated from
those constraints.

**Verification:** A strict audit of the locked environment reports no known
vulnerabilities.

### SEC-002 — Mutable CI actions and broad default credentials

**Severity before fix:** Medium

**Affected files:** `.github/workflows/ci.yml`,
`.github/workflows/security.yml`

The workflows used mutable action tags and did not explicitly minimize the
repository token. A compromised upstream tag or an unnecessarily reusable
checkout credential would have expanded the impact of a CI compromise.

**Fix:** Every third-party action is pinned to a full commit SHA. Workflow
permissions are read-only, and checkout does not persist Git credentials.
The dependency auditor is version-pinned and runs in strict mode without
invoking pip.

**Verification:** Zizmor reports no workflow findings.

### SEC-003 — Workshop files could inherit a permissive umask

**Severity before fix:** Medium

**Affected file:** `harness/workshop.py`

The workshop readiness file contains a short-lived bearer URL, and the response
file contains the user's design decisions. Atomic writes were already used,
but file permissions depended on the caller's umask.

**Fix:** Temporary workshop files are created exclusively with mode `0600`,
atomically replaced, and explicitly returned to mode `0600` after replacement.
A platform-aware regression test verifies the permissions.

### SEC-004 — Invisible Unicode in sanitizer source

**Severity before fix:** Low

**Affected file:** `harness/_sanitize.py`

The sanitizer's denylist contained literal bidirectional and zero-width
characters. The behavior was correct, but the source was unnecessarily hard to
review and triggered static-analysis warnings.

**Fix:** The same characters are expressed as visible Unicode escape
sequences. Runtime behavior is unchanged.

## Security boundaries verified

- **Network capture:** Keen rejects unsupported schemes, loopback, private,
  link-local, reserved, carrier-grade NAT, and cloud-metadata addresses.
  Redirects and every browser subrequest are revalidated to resist DNS
  rebinding and redirect-based SSRF. WebSockets are blocked during guarded
  capture.
- **Authentication:** The default path accepts a closed JSON action language
  with environment-variable references. Arbitrary Python authentication
  scripts require an explicit unsafe flag and, by default, must live inside
  the current project.
- **Local workshop:** The server binds only to `127.0.0.1`, uses a random
  bearer token, rejects non-loopback host headers, limits request size, accepts
  JSON only, and permits one successful submission. Its page uses a strict
  Content Security Policy and no remote assets.
- **Generated reports:** Untrusted page text and URLs are escaped. Unsafe
  schemes and traversal outside the report directory are rejected. Workshop
  rendering uses DOM construction and `textContent`, not HTML injection.
- **Secrets:** Repository history and the full unpublished tree were scanned.
  No actual credentials were found, and GitHub secret scanning has no open
  alerts.
- **Repository:** Dependabot vulnerability alerts and private vulnerability
  reporting are enabled.

## Residual risk and user responsibility

Keen deliberately captures rendered pages. Screenshots, DOM evidence, and HTML
reports may therefore contain private product or account data. Keep disposable
`.keen/` runs out of source control and share them only with authorized people.

The `--unsafe-auth-script` option runs arbitrary local Python by design. It
should only be used with an exact script the user has reviewed and approved.

This was a source, dependency, workflow, and adversarial test review. It was not
an independent penetration test of every browser or operating-system
configuration.

## Release verification

The reviewed state passed:

- 1,012 automated tests, with one intentional skip
- Ruff lint and format checks
- Mypy across the runtime package
- 240 targeted adversarial tests
- prompt, skill-contract, and direction-workshop evaluations
- clean wheel build, isolated installation, runtime-asset check, and CLI smoke
  test
- strict locked-dependency audit with no known vulnerabilities
- secret scan with no findings
- Bandit static analysis with no actionable findings
- Zizmor workflow analysis with no findings
- Claude and Codex plugin bundle validation
