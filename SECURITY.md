# Security policy

Keen is a local-first developer tool. During beta, security fixes are applied
to the latest release and the current `main` branch.

## Report a vulnerability privately

Use **Report a vulnerability** in the repository's GitHub Security tab. Private
vulnerability reporting is enabled for this repository. Please do not open a
public issue for a suspected vulnerability.

Include the affected version, reproduction steps, expected impact, and any
suggested mitigation. Do not include real credentials, session cookies, private
site captures, or other sensitive customer data.

## Protect local review data

Keen reports and screenshots can contain content from the page being reviewed.
Keep disposable `.keen/` capture runs out of source control and share them only
with people who are allowed to see the source page.

Use declarative authentication steps with environment-variable references when
possible. Never commit authentication values or browser storage state. The
Python authentication-script escape hatch executes arbitrary code and should
only be used for an exact file that has been reviewed and explicitly approved.

Direction-workshop files are temporary. Keep them in an operating-system
temporary directory; do not commit the readiness file because it contains the
short-lived local bearer URL.

The latest completed release review is
[Keen 0.8.1 security review](plugins/keen/docs/security-review-0.8.1.md).
