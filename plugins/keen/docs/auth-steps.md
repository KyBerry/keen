# Declarative auth steps (`--auth-steps`)

The `--auth-steps PATH` flag points the harness at a JSON file that describes
your login flow as a list of named steps. The harness validates the file
against a closed schema, then dispatches each step through a fixed Playwright
action whitelist. No user-provided Python is executed.

This is the **preferred** way to authenticate. Use `--auth-script` only when
your flow legitimately cannot be expressed in the DSL.

Every Playwright-backed action accepts `timeout_ms` only where the table lists
it. The value must be between 1 and 60,000 milliseconds. `--allow-internal`
applies consistently to the review target, declarative `goto` steps, cookies,
redirects, and browser subresources; metadata and carrier-grade NAT ranges
remain blocked even when local/private targets are explicitly allowed.

For a one-time local review that cannot be expressed safely as steps, use
`--interactive-auth`. Keen opens headed Chromium, waits for the user to sign
in, and reuses that ephemeral browser state for the requested capture matrix.
Keen does not write the resulting cookies or storage values into review
artifacts. The option requires an interactive terminal and is mutually
exclusive with `--auth-steps` and `--auth-script`.

```bash
keen review "http://localhost:3000/account" \
  --allow-internal \
  --interactive-auth \
  --expect-selector "[data-account-ready]"
```

Do not remove or weaken product authentication merely to make a page
capturable. A server-side, development-only route can be appropriate for a
true design sandbox, but real authenticated product screens should be reviewed
through their real access path.

## Why declarative is preferred

`--auth-script` loads a `.py` file and runs `exec_module` on it, so any code
in that file runs with the same privileges as the harness. We gate it behind
`--unsafe-auth-script` plus cwd containment, but that is a *defensive* layer
— the path itself is dangerous: a typo or a compromised script can read your
filesystem, exfiltrate env vars, or pivot to anywhere in the network.

`--auth-steps` removes that path entirely:

- The JSON is parsed with `json.loads` (no `eval`).
- Every step's `action` is checked against a frozen whitelist.
- Selectors are length-capped at 512 chars and rejected if they contain
  backticks or `${}` interpolation tokens.
- Credentials are read from environment variables (`value_env`), never
  embedded in the file, and never logged.
- The `eval_safe` action accepts only five regex-matched expression forms,
  and parameters are passed through `page.evaluate(expr, arg)` so the raw
  string is never interpolated into the JS source.

## Schema

Top-level shape:

```json
{
  "steps": [ /* one or more step objects */ ]
}
```

Each step is an object with a required `"action"` key. Unknown actions are
rejected with a list of supported ones.

## Action whitelist

### `goto`

Navigate to a URL.

| key          | type    | required | default | notes                                 |
| ------------ | ------- | -------- | ------- | ------------------------------------- |
| `url`        | string  | yes      | —       | non-empty                             |
| `timeout_ms` | integer | no       | 30000   | per-attempt timeout                   |

### `wait_for_selector`

Wait until a selector matches before continuing.

| key          | type    | required | default                                 | notes |
| ------------ | ------- | -------- | --------------------------------------- | ----- |
| `selector`   | string  | yes      | —                                       | 1..512 chars, no backticks or `${}` |
| `timeout_ms` | integer | no       | 15000                                   |       |
| `state`      | string  | no       | (Playwright default — `visible`)        | one of `visible`, `attached`, `hidden`, `detached` |

### `wait_for_url`

Wait until the page URL matches the given glob/regex (per Playwright's
`page.wait_for_url`).

| key          | type    | required | default | notes |
| ------------ | ------- | -------- | ------- | ----- |
| `pattern`    | string  | yes      | —       |       |
| `timeout_ms` | integer | no       | 15000   |       |

### `fill`

Type a value into an input. Pass **exactly one** of `value` or `value_env`.

| key          | type    | required | default | notes                                  |
| ------------ | ------- | -------- | ------- | -------------------------------------- |
| `selector`   | string  | yes      | —       |                                        |
| `value`      | string  | conditional | —    | literal value; logs a warning (prefer `value_env` for credentials) |
| `value_env`  | string  | conditional | —    | name of env var to read; missing → `ValueError` |
| `timeout_ms` | integer | no       | 15000   |                                        |

### `click`

Click an element.

| key          | type    | required | default | notes                                |
| ------------ | ------- | -------- | ------- | ------------------------------------ |
| `selector`   | string  | yes      | —       |                                      |
| `button`     | string  | no       | `left`  | one of `left`, `right`, `middle`     |
| `timeout_ms` | integer | no       | 15000   |                                      |

### `press`

Press a key inside a focused element (e.g. `Enter`, `Tab`).

| key          | type    | required | default | notes |
| ------------ | ------- | -------- | ------- | ----- |
| `selector`   | string  | yes      | —       |       |
| `key`        | string  | yes      | —       | Playwright key name |
| `timeout_ms` | integer | no       | 15000   |       |

### `check` / `uncheck`

Toggle a checkbox or radio.

| key          | type    | required | default | notes |
| ------------ | ------- | -------- | ------- | ----- |
| `selector`   | string  | yes      | —       |       |
| `timeout_ms` | integer | no       | 15000   |       |

### `select_option`

Select an option from a `<select>`. Pass **exactly one** of `value` or
`values`.

| key          | type             | required    | default | notes |
| ------------ | ---------------- | ----------- | ------- | ----- |
| `selector`   | string           | yes         | —       |       |
| `value`      | string           | conditional | —       | single-select |
| `values`     | list of strings  | conditional | —       | multi-select |
| `timeout_ms` | integer          | no          | 15000   |       |

### `set_storage`

Set a `localStorage` value from a `KEEN_*` environment variable. The
secret stays out of the JSON file and is parameter-bound into the browser.

| key         | type   | required | notes |
| ----------- | ------ | -------- | ----- |
| `key`       | string | yes      | 1..128 safe identifier characters |
| `value_env` | string | yes      | must start with `KEEN_` |

### `set_cookie`

Set a cookie for the page's current URL from a `KEEN_*` environment
variable. A `goto` step must establish the origin first.

| key         | type   | required | notes |
| ----------- | ------ | -------- | ----- |
| `name`      | string | yes      | 1..128 safe identifier characters |
| `value_env` | string | yes      | must start with `KEEN_` |

### `eval_safe`

Run a narrow expression against the page. Only these five forms are allowed:

| form                                                         | use case                       |
| ------------------------------------------------------------ | ------------------------------ |
| `document.cookie = '<single-quoted string>'`                | cookie-based auth              |
| `localStorage.setItem('<key>', '<value>')`                  | JWT / OAuth token storage      |
| `localStorage.removeItem('<key>')`                          | clear stored credentials       |
| `sessionStorage.setItem('<key>', '<value>')`                | session-scoped token           |
| `sessionStorage.removeItem('<key>')`                        | clear session token            |

Anything else (function calls, assignments to other globals, complex
expressions) is rejected with `ValueError`. Keys and values are parsed out
of the regex match and passed to `page.evaluate(expr, arg)` as parameters,
never interpolated into the JS source.

Need something more complex? Fall back to `--auth-script` (with the unsafe
gate).

### `sleep_ms`

Sleep for `ms` milliseconds. Capped at 5000 (5 seconds); larger values are
rejected. Use this sparingly — prefer `wait_for_selector` or `wait_for_url`.

| key   | type    | required | default | notes        |
| ----- | ------- | -------- | ------- | ------------ |
| `ms`  | integer | yes      | —       | 0..5000      |

## Examples

### Form login

```json
{
  "steps": [
    { "action": "goto",              "url":      "https://example.com/login" },
    { "action": "wait_for_selector", "selector": "#email" },
    { "action": "fill",              "selector": "#email",    "value_env": "KEEN_EMAIL" },
    { "action": "fill",              "selector": "#password", "value_env": "KEEN_PASSWORD" },
    { "action": "click",             "selector": "button[type=submit]" },
    { "action": "wait_for_url",      "pattern":  "**/dashboard" }
  ]
}
```

Run it:

```bash
export KEEN_EMAIL="me@example.com"
export KEEN_PASSWORD="…"
keen capture https://example.com/dashboard \
  --auth-steps ./auth.json
```

### OAuth token in localStorage

For SPAs that read a token from `localStorage` at boot, keep the token in an
environment variable and set it without embedding the secret in JSON:

```json
{
  "steps": [
    { "action": "goto",        "url": "https://example.com" },
    { "action": "set_storage", "key": "auth_token", "value_env": "KEEN_AUTH_TOKEN" }
  ]
}
```

```bash
export KEEN_AUTH_TOKEN="$(your-token-provider)"
keen capture https://example.com/app --auth-steps ./auth.json
```

### Cookie-based auth

If you already have a session cookie from elsewhere:

```json
{
  "steps": [
    { "action": "goto",       "url": "https://example.com" },
    { "action": "set_cookie", "name": "session", "value_env": "KEEN_SESSION" }
  ]
}
```

## Migrating from `--auth-script`

Most `--auth-script` files look like this:

```python
# auth.py
async def login(page):
    await page.goto("https://example.com/login")
    await page.fill("#email", "me@example.com")
    await page.fill("#password", "supersecret")
    await page.click("button[type=submit]")
    await page.wait_for_url("**/dashboard")
```

The equivalent declarative file:

```json
{
  "steps": [
    { "action": "goto",         "url":      "https://example.com/login" },
    { "action": "fill",         "selector": "#email",    "value_env": "KEEN_EMAIL" },
    { "action": "fill",         "selector": "#password", "value_env": "KEEN_PASSWORD" },
    { "action": "click",        "selector": "button[type=submit]" },
    { "action": "wait_for_url", "pattern":  "**/dashboard" }
  ]
}
```

Then export the two `KEEN_*` variables and run with `--auth-steps ./auth.json`.

If your script does *anything beyond* the action whitelist — captcha solving,
2FA prompts, multi-tab handoffs, calling internal libraries — keep using
`--auth-script` (with `--unsafe-auth-script`).

## Security model

| concern                      | guarantee                                                  |
| ---------------------------- | ---------------------------------------------------------- |
| Arbitrary Python execution   | None — file is parsed by `json.loads`, never `exec`.       |
| Arbitrary JS execution       | Structured secret actions use parameter binding; legacy `eval_safe` is limited to five regex-matched forms. |
| Selector injection           | Selectors are length-capped at 512 chars and reject `` ` `` and `${`. |
| Credential leakage in logs   | `value_env` reads the env var; the **name** is logged, the **value** never is. Literal `value` is permitted but warned about. |
| Missing env vars             | `ValueError("env var NAME not set for fill step")` — the value itself never enters the error message. |
| `eval_safe` whitelist bypass | Every alternative form goes through the same regex gate; mismatches raise `ValueError`. |
| Interactive session artifacts | Keen does not serialize captured cookies, localStorage, or sessionStorage into the run directory. |

## Capture integrity after authentication

After authentication, Keen revisits the requested target and verifies the final
surface. By default, the final URL must preserve the requested origin, path,
query string, and fragment; a trailing slash is normalized and a same-host
HTTP-to-HTTPS upgrade is accepted. A changed URL blocks review unless the
intended destination was declared with `--expect-url`. An explicit path-glob
expectation can match a broader path family: if that pattern omits a query or
fragment, either is allowed; if it includes one, it must match exactly. Use
`--expect-selector` when the protected and unprotected surfaces share a URL.

`--allow-internal` does not grant the captured page access to every service on
the machine or private network. Keen permits only the exact origins named by
the target, a non-glob `--expect-url`, and `goto` auth steps. Declare a separate
development API explicitly with repeatable
`--allow-origin http://127.0.0.1:PORT` options. Each value must be an origin
only, without a path, query, fragment, or credentials.

For `file:` review, `--allow-file` authorizes the exact target and explicit
auth-step documents. Same-directory publication assets can load, but sibling
documents, unrecognized asset types, traversal, and symlink escapes cannot.
Use a local HTTP server for projects whose source directory also contains
private files.

A blocked run keeps a screenshot and DOM dump for diagnosis, writes
`agent-brief.json` with `review_status.status = "blocked"`, and does not
decompose, score, or produce a normal report. Those diagnostic artifacts also
cannot be reviewed later with `keen audit`.

The `--auth-script` escape hatch still exists, gated behind
`--unsafe-auth-script` plus cwd containment (`--unsafe-auth-script-anywhere`
to relax). It is now documented as the path of last resort.
