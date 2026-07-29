"""Capture stage: take screenshots and dump enough DOM information to decompose later.

Output layout under <out-dir>/:
    screens/<route>-<viewport>-<state>.png      full-page screenshot
    dom/<route>-<viewport>-<state>.json         DOM dump

The DOM dump is the contract between this stage and `decompose`. Its shape is
defined by INSTRUMENT_JS below — keep them in sync. The dump captures, for every
visible element:
    - tag, id, classList, role, name (accessible name), label
    - bounding box (x, y, width, height in CSS pixels at the captured DPR)
    - computed styles relevant to design review
    - text content (truncated)
    - parent index (so the tree can be rebuilt)
    - hasAlt (distinguishes alt="" from missing alt)
    - inputmode, autocomplete, required, ariaModal, ariaCurrent

Plus, per dump:
    - meta.devicePixelRatio
    - meta.viewport_size
    - focus_coverage: whether any user CSS defines :focus-visible rules
                      and how many rules target interactive elements
    - focused_index: index of the element actually focused for state=focus

Authentication (preferred): pass --auth-steps pointing to a JSON file with a
declarative list of login steps (see docs/auth-steps.md). The harness
validates the schema and executes a fixed whitelist of Playwright actions —
no arbitrary Python.

Authentication (escape hatch): pass --auth-script pointing to a Python file
that exposes `async def login(page) -> None`. This runs arbitrary Python and
is gated behind --unsafe-auth-script in the CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlparse

from harness import _artifacts as artifacts_mod
from harness import _safeio as safeio_mod
from harness._assets import asset_path
from harness._sanitize import sanitize_dom_payload
from harness._urlsafe import (
    NetworkOrigin,
    file_document_path,
    file_scope_root,
    network_origin,
    redact_url,
    revalidate_target_at_request_time,
    validate_target,
)

if TYPE_CHECKING:
    from playwright.async_api import Page


logger = logging.getLogger("keen")

_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_CAPTURE_CONCURRENCY = 8
MAX_SCREENSHOT_PIXELS = 16_000_000
MAX_SCREENSHOT_WIDTH_PX = 8_000
MAX_SCREENSHOT_HEIGHT_PX = 20_000
MAX_SESSION_STORAGE_ENTRIES = 256
MAX_SESSION_STORAGE_BYTES = 1_048_576
MAX_AUTH_STORAGE_STATE_BYTES = 8 * 1_048_576

_AX_NAME_TAGS = frozenset(
    {
        "a",
        "button",
        "details",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "img",
        "input",
        "select",
        "summary",
        "textarea",
    }
)
_AX_MARKER_ATTR = "data-keen-ax-index"
_AX_SNAPSHOT_CONCURRENCY = 20
_AX_MAX_CANDIDATES = 500
_AX_TOTAL_TIMEOUT_SECONDS = 20.0


class CaptureFailure(RuntimeError):
    """Raised when one or more requested captures fail."""


class CaptureBlocked(RuntimeError):
    """Raised after diagnostic artifacts are saved for an unexpected surface."""

    def __init__(
        self,
        *,
        reason_code: str,
        message: str,
        screen_path: Path,
        dom_path: Path,
        expected_url: str | None = None,
        observed_url: str | None = None,
        expected_selector: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.screen_path = screen_path
        self.dom_path = dom_path
        self.expected_url = expected_url
        self.observed_url = observed_url
        self.expected_selector = expected_selector


def _browser_launch_failure_message(exc: Exception) -> str:
    if "Executable doesn't exist" in str(exc):
        return (
            "Chromium is not installed for this Playwright environment; "
            "run `python -m playwright install chromium` with the same Python interpreter"
        )
    return f"could not launch Chromium: {exc}"


def _parse_aria_snapshot_root(snapshot: str) -> tuple[str, str] | None:
    """Extract the root role and computed name from a Playwright ARIA snapshot.

    Playwright serializes an accessibility node as ``- role "name":``.  The
    quoted name uses JSON-compatible escaping, so decoding it through
    :func:`json.loads` preserves quotes and non-ASCII text without evaluating
    page-controlled content.  ``None`` means the locator did not expose a root
    accessibility node; an empty name is a valid parsed result.
    """
    if not isinstance(snapshot, str) or not snapshot.strip():
        return None
    first = snapshot.lstrip().splitlines()[0].strip()
    match = re.match(
        r'^-\s+([^\s:\[]+)(?:\s+"((?:[^"\\]|\\.)*)")?(?:\s+\[[^\]]*\])?:?$',
        first,
    )
    if not match:
        return None
    role = match.group(1)
    encoded_name = match.group(2)
    if encoded_name is None:
        return role, ""
    try:
        name = json.loads(f'"{encoded_name}"')
    except json.JSONDecodeError:
        # The snapshot grammar should remain JSON-compatible, but a graceful
        # fallback keeps capture available if Playwright adds a new escape.
        name = encoded_name.replace(r"\"", '"').replace(r"\\", "\\")
    return role, str(name)


async def _enrich_accessibility_names(page: Page, dom: dict[str, Any]) -> dict[str, Any]:
    """Replace heuristic names with names computed by the browser AX tree.

    The in-page instrumentation retains the visible element array on a private
    window property.  After the screenshot is written, this helper temporarily
    marks only semantic/name-bearing elements, asks Playwright for each root
    ARIA snapshot with bounded concurrency, then removes every marker.  The
    screenshot therefore cannot be affected by the temporary attributes.

    Failures are isolated per element.  A page re-render or unsupported node
    falls back to the deterministic DOM heuristic already stored in ``name``.
    """
    elements = dom.get("elements")
    if not isinstance(elements, list):
        return {"eligible": 0, "attempted": 0, "enriched": 0, "complete": False}
    eligible = [
        elem
        for elem in elements
        if isinstance(elem, dict)
        and isinstance(elem.get("index"), int)
        and (str(elem.get("tag") or "").lower() in _AX_NAME_TAGS or bool(elem.get("role")))
    ]
    candidates = eligible[:_AX_MAX_CANDIDATES]
    if not candidates:
        return {"eligible": 0, "attempted": 0, "enriched": 0, "complete": True}

    indices = [int(elem["index"]) for elem in candidates]
    await page.evaluate(
        """
        ([indices, marker]) => {
          const visible = window.__keenVisibleElements || [];
          for (const index of indices) {
            const element = visible[index];
            if (element && element.isConnected) element.setAttribute(marker, String(index));
          }
        }
        """,
        [indices, _AX_MARKER_ATTR],
    )

    semaphore = asyncio.Semaphore(_AX_SNAPSHOT_CONCURRENCY)
    enriched_count = 0

    async def enrich_one(elem: dict[str, Any]) -> None:
        nonlocal enriched_count
        index = int(elem["index"])
        selector = f'[{_AX_MARKER_ATTR}="{index}"]'
        try:
            async with semaphore:
                snapshot = await page.locator(selector).aria_snapshot(timeout=1_500)
        except Exception:
            return
        parsed = _parse_aria_snapshot_root(snapshot)
        if parsed is None:
            return
        _role, name = parsed
        elem["name"] = name
        elem["nameSource"] = "browser-accessibility-tree"
        enriched_count += 1

    timed_out = False
    try:
        try:
            await asyncio.wait_for(
                asyncio.gather(*(enrich_one(elem) for elem in candidates)),
                timeout=_AX_TOTAL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            timed_out = True
    finally:
        await page.evaluate(
            """
            (marker) => {
              const visible = window.__keenVisibleElements || [];
              for (const element of visible) {
                if (element && element.removeAttribute) element.removeAttribute(marker);
              }
              try { delete window.__keenVisibleElements; } catch (error) {
                window.__keenVisibleElements = undefined;
              }
            }
            """,
            _AX_MARKER_ATTR,
        )
    failed_count = len(candidates) - enriched_count
    complete = len(eligible) <= _AX_MAX_CANDIDATES and not timed_out and failed_count == 0
    return {
        "eligible": len(eligible),
        "attempted": len(candidates),
        "enriched": enriched_count,
        "failed": failed_count,
        "complete": complete,
        "reason": (
            "accessibility-name-timeout"
            if timed_out
            else "accessibility-name-cap"
            if len(eligible) > _AX_MAX_CANDIDATES
            else "accessibility-name-miss"
            if failed_count
            else None
        ),
    }


def _validate_artifact_id(value: str, *, kind: str) -> str:
    """Validate an identifier before it becomes part of an artifact path."""
    if not _ARTIFACT_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            f"invalid {kind} {value!r}; use 1-64 letters, numbers, dots, underscores, or hyphens"
        )
    return value


def _screenshot_options(
    cfg: CaptureConfig,
    preset: dict[str, int],
    dom: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bound screenshot memory while preserving as much full-page evidence as possible."""
    if not cfg.full_page:
        return {"full_page": False}, {"complete": True, "reason": None}

    document_size = dom.get("documentSize") or {}
    document_width = max(int(document_size.get("width") or preset["width"]), 1)
    document_height = max(int(document_size.get("height") or preset["height"]), 1)
    dpr = max(float(preset.get("deviceScaleFactor") or 1), 1.0)
    full_width = min(document_width, MAX_SCREENSHOT_WIDTH_PX)
    full_pixel_height_cap = max(int(MAX_SCREENSHOT_PIXELS / (full_width * dpr * dpr)), 1)
    full_height = min(document_height, MAX_SCREENSHOT_HEIGHT_PX, full_pixel_height_cap)
    if full_width >= document_width and full_height >= document_height:
        return {"full_page": True}, {"complete": True, "reason": None}

    # Python Playwright clips screenshots to the current viewport. When a full
    # page exceeds our memory cap, request an honest top-viewport capture
    # instead of claiming that a taller off-viewport clip was written.
    viewport_width = max(int(preset["width"]), 1)
    viewport_height = max(int(preset["height"]), 1)
    capture_width = min(document_width, viewport_width, MAX_SCREENSHOT_WIDTH_PX)
    pixel_height_cap = max(int(MAX_SCREENSHOT_PIXELS / (capture_width * dpr * dpr)), 1)
    capture_height = min(
        document_height,
        viewport_height,
        MAX_SCREENSHOT_HEIGHT_PX,
        pixel_height_cap,
    )
    reasons = []
    if capture_width < document_width:
        reasons.append("width-cap")
    if capture_height < document_height:
        reasons.append("height-or-pixel-cap")
    return (
        {
            "full_page": False,
            "clip": {
                "x": 0,
                "y": 0,
                "width": capture_width,
                "height": capture_height,
            },
        },
        {
            "complete": False,
            "reason": "+".join(reasons),
            "document_css_px": {"width": document_width, "height": document_height},
            "captured_css_px": {"width": capture_width, "height": capture_height},
            "max_device_pixels": MAX_SCREENSHOT_PIXELS,
        },
    )


# -- Configuration ---------------------------------------------------------

# Built-in fallback presets (used only if config/viewports.json can't be loaded).
DEFAULT_VIEWPORT_PRESETS: dict[str, dict[str, int]] = {
    "mobile": {"width": 390, "height": 844, "deviceScaleFactor": 2},
    "tablet": {"width": 834, "height": 1194, "deviceScaleFactor": 2},
    "desktop": {"width": 1440, "height": 900, "deviceScaleFactor": 1},
    "wide": {"width": 1920, "height": 1080, "deviceScaleFactor": 1},
}


def load_viewport_presets(config_path: Path | None = None) -> dict[str, dict[str, int]]:
    """Load viewport presets from config/viewports.json or a user-provided path."""
    if config_path is None:
        config_path = asset_path("config", "viewports.json")
    if not config_path.exists():
        logger.warning("viewports.json missing at %s; falling back to defaults", config_path)
        return dict(DEFAULT_VIEWPORT_PRESETS)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "viewports.json: %s; falling back to defaults for presets",
            e,
        )
        return dict(DEFAULT_VIEWPORT_PRESETS)

    presets_raw = data.get("presets")
    if not isinstance(presets_raw, dict):
        logger.warning("viewports.json: 'presets' missing or wrong type; falling back to defaults")
        return dict(DEFAULT_VIEWPORT_PRESETS)

    out: dict[str, dict[str, int]] = {}
    for name, preset in presets_raw.items():
        if name.startswith("_"):
            continue
        try:
            out[name] = {
                "width": int(preset["width"]),
                "height": int(preset["height"]),
                "deviceScaleFactor": int(preset.get("deviceScaleFactor", 1)),
            }
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(
                "viewports.json: skipping preset '%s' (%s); falling back to defaults for this key",
                name,
                e,
            )
    return out or dict(DEFAULT_VIEWPORT_PRESETS)


def load_states(config_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load state definitions (with setup_steps DSL) from config/states.json."""
    if config_path is None:
        config_path = asset_path("config", "states.json")
    if not config_path.exists():
        logger.warning("states.json missing at %s; falling back to default state only", config_path)
        return {"default": {"setup_steps": []}}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "states.json: %s; falling back to defaults for states",
            e,
        )
        return {"default": {"setup_steps": []}}

    states_raw = data.get("states")
    if not isinstance(states_raw, dict):
        logger.warning(
            "states.json: 'states' missing or wrong type; falling back to default state only"
        )
        return {"default": {"setup_steps": []}}
    return {name: spec for name, spec in states_raw.items() if not name.startswith("_")}


@dataclass
class CaptureConfig:
    target: str
    viewports: list[str] = field(default_factory=lambda: ["mobile", "tablet", "desktop"])
    states: list[str] = field(default_factory=lambda: ["default"])
    full_page: bool = True
    # `wait_selector` remains the programmatic name for backward
    # compatibility; the CLI also exposes the clearer --expect-selector alias.
    wait_selector: str | None = None
    # Auth: prefer --auth-steps (declarative JSON DSL). --auth-script remains
    # as an escape hatch but requires CLI-side --unsafe-auth-script gating.
    auth_steps_path: Path | None = None
    auth_script: Path | None = None
    outdir: Path = field(default_factory=lambda: Path("./.keen/captures"))
    viewport_config: Path | None = None
    states_config: Path | None = None
    # Hardening knobs
    allow_internal: bool = False
    allow_file: bool = False
    goto_timeout_ms: int = 30_000
    # Real-world capture knobs (CLI wiring is a follow-up; agents/scripts can set
    # these directly on the dataclass).
    #
    # settle_ms: extra ms to wait AFTER `domcontentloaded` and AFTER any
    # `wait_selector` resolves, to let JS-driven hydration (React, Vue, Svelte)
    # paint real content over skeletons. 0 = no extra wait. Recommended 500-2000
    # for SPA marketing pages.
    settle_ms: int = 0
    # dismiss_banners: if True, after settle the harness tries a closed list of
    # common cookie/GDPR banner accept-button selectors with a 500ms per-selector
    # timeout and clicks the first match. Logs which (if any) was matched.
    dismiss_banners: bool = False
    # New 0.8.1 options stay after every 0.8.0 init field so existing
    # positional CaptureConfig construction keeps its original meaning.
    # When omitted, the requested target (including query/hash routes, while
    # allowing canonical URL differences) is the expected final surface.
    expect_url: str | None = None
    # Opens a headed browser and waits for the user to finish authentication.
    # Keen does not serialize the resulting browser state into project artifacts.
    interactive_auth: bool = False
    allow_origins: list[str] = field(default_factory=list)
    # Computed during preflight from explicit file targets only. Browser
    # redirects and subresources must stay inside these roots.
    file_request_roots: tuple[Path, ...] = field(default_factory=tuple, init=False, repr=False)
    file_document_paths: tuple[Path, ...] = field(default_factory=tuple, init=False, repr=False)
    internal_request_origins: tuple[NetworkOrigin, ...] = field(
        default_factory=tuple, init=False, repr=False
    )


@dataclass
class AuthSession:
    """Ephemeral browser state cloned into each requested capture context."""

    storage_state: dict[str, Any]
    session_storage: dict[str, dict[str, str]] = field(default_factory=dict)


def _explicit_navigation_urls(
    target: str,
    expect_url: str | None = None,
    auth_steps: list[dict[str, Any]] | None = None,
) -> list[str]:
    urls = [target]
    if expect_url is not None and "*" not in expect_url:
        urls.append(expect_url)
    if auth_steps is not None:
        urls.extend(
            str(step["url"])
            for step in auth_steps
            if step.get("action") == "goto" and isinstance(step.get("url"), str)
        )
    return urls


def _derive_file_request_policy(
    target: str,
    expect_url: str | None = None,
    auth_steps: list[dict[str, Any]] | None = None,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Grant exact documents plus their ordinary publication-asset roots."""
    roots: list[Path] = []
    documents: list[Path] = []
    for url in _explicit_navigation_urls(target, expect_url, auth_steps):
        if urlparse(url).scheme.lower() != "file":
            continue
        root = file_scope_root(url)
        if root not in roots:
            roots.append(root)
        document = file_document_path(url)
        if document not in documents:
            documents.append(document)
    return tuple(roots), tuple(documents)


def _derive_internal_request_origins(
    target: str,
    expect_url: str | None = None,
    auth_steps: list[dict[str, Any]] | None = None,
    allow_origins: list[str] | None = None,
) -> tuple[NetworkOrigin, ...]:
    """Authorize only explicitly named HTTP(S) origins for internal access."""
    origins: list[NetworkOrigin] = []
    urls = _explicit_navigation_urls(target, expect_url, auth_steps)
    urls.extend(allow_origins or [])
    for url in urls:
        if urlparse(url).scheme.lower() not in {"http", "https"}:
            continue
        origin = network_origin(url)
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


def _canonical_url_identity(
    url: str,
) -> tuple[str, str, int | None, str, str, str] | None:
    """Return the stable URL fields used to recognize the requested surface."""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if hostname:
            hostname = hostname.encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if scheme not in {"http", "https", "file"}:
        return None
    if scheme == "file":
        return scheme, "", None, parsed.path or "/", parsed.query, parsed.fragment
    if not hostname:
        return None
    if port is None:
        port = 80 if scheme == "http" else 443
    # Chromium serializes Unicode paths with UTF-8 percent encoding. Preserve
    # already-escaped reserved delimiters while normalizing Unicode and hex
    # case so `/café` and `/caf%C3%A9` identify the same route.
    path = quote(parsed.path or "/", safe="/:@!$&'()*+,;=-._~%")
    path = re.sub(
        r"%[0-9a-fA-F]{2}",
        lambda match: match.group(0).upper(),
        path,
    )
    if path != "/":
        path = path.rstrip("/")
    query = quote(parsed.query, safe="!$&'()*+,;=:@/?-._~%")
    fragment = quote(parsed.fragment, safe="!$&'()*+,;=:@/?-._~%")
    query = re.sub(r"%[0-9a-fA-F]{2}", lambda match: match.group(0).upper(), query)
    fragment = re.sub(r"%[0-9a-fA-F]{2}", lambda match: match.group(0).upper(), fragment)
    return scheme, hostname, port, path, query, fragment


def _urls_equivalent(expected: str, observed: str) -> bool:
    """Match canonical URLs while allowing a same-host HTTP→HTTPS upgrade."""
    expected_id = _canonical_url_identity(expected)
    observed_id = _canonical_url_identity(observed)
    if expected_id is None or observed_id is None:
        return False
    if expected_id == observed_id:
        return True
    (
        expected_scheme,
        expected_host,
        expected_port,
        expected_path,
        expected_query,
        expected_fragment,
    ) = expected_id
    (
        observed_scheme,
        observed_host,
        observed_port,
        observed_path,
        observed_query,
        observed_fragment,
    ) = observed_id
    return (
        expected_scheme == "http"
        and observed_scheme == "https"
        and expected_host == observed_host
        and expected_port == 80
        and observed_port == 443
        and expected_path == observed_path
        and expected_query == observed_query
        and expected_fragment == observed_fragment
    )


def _url_matches_expectation(target: str, observed: str, expect_url: str | None) -> bool:
    """Return whether the browser landed on the explicitly intended surface."""
    expected = expect_url or target
    if "*" in expected:
        expected_id = _canonical_url_identity(expected)
        observed_id = _canonical_url_identity(observed)
        if expected_id is None or observed_id is None:
            return False
        (
            expected_scheme,
            expected_host,
            expected_port,
            expected_path,
            expected_query,
            expected_fragment,
        ) = expected_id
        (
            observed_scheme,
            observed_host,
            observed_port,
            observed_path,
            observed_query,
            observed_fragment,
        ) = observed_id
        same_authority = (
            expected_scheme == observed_scheme
            and expected_host == observed_host
            and expected_port == observed_port
        )
        upgraded_authority = (
            expected_scheme == "http"
            and observed_scheme == "https"
            and expected_host == observed_host
            and expected_port == 80
            and observed_port == 443
        )
        return (
            (same_authority or upgraded_authority)
            and fnmatchcase(observed_path, expected_path)
            and (not expected_query or observed_query == expected_query)
            and (not expected_fragment or observed_fragment == expected_fragment)
        )
    return _urls_equivalent(expected, observed)


def _validate_capture_expectations(cfg: CaptureConfig) -> None:
    """Fail before browser launch for unusable expectation inputs."""
    if cfg.expect_url is not None:
        if not isinstance(cfg.expect_url, str) or not cfg.expect_url.strip():
            raise ValueError("--expect-url must be a non-empty URL or URL glob")
        if len(cfg.expect_url) > 2_048:
            raise ValueError("--expect-url exceeds the 2048-character limit")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cfg.expect_url):
            raise ValueError("--expect-url must not contain control characters")
        parsed_expectation = urlparse(cfg.expect_url)
        if parsed_expectation.scheme not in {"http", "https", "file"}:
            raise ValueError("--expect-url must be an absolute http(s)/file URL or URL glob")
        if parsed_expectation.scheme in {"http", "https"} and not parsed_expectation.netloc:
            raise ValueError("--expect-url must include an explicit host")
        if "*" in parsed_expectation.scheme or "*" in parsed_expectation.netloc:
            raise ValueError("--expect-url wildcards are allowed only in the path")
        if "*" in parsed_expectation.query or "*" in parsed_expectation.fragment:
            raise ValueError("--expect-url wildcards are allowed only in the path")
        if parsed_expectation.username is not None or parsed_expectation.password is not None:
            raise ValueError("--expect-url must not contain URL userinfo")
        if "*" not in cfg.expect_url and _canonical_url_identity(cfg.expect_url) is None:
            raise ValueError("--expect-url is not a valid absolute URL")
    if cfg.wait_selector is not None:
        _validate_selector(cfg.wait_selector, where="--expect-selector")


def _url_expectation_failure(cfg: CaptureConfig, observed_url: str) -> dict[str, Any] | None:
    """Build a redacted coverage failure when navigation reached another page."""
    if _url_matches_expectation(cfg.target, observed_url, cfg.expect_url):
        return None
    expected = cfg.expect_url or redact_url(cfg.target)
    return {
        "complete": False,
        "status": "blocked",
        "reason_code": "unexpected-url",
        "message": "The browser reached a different URL than the requested review surface.",
        "target": redact_url(cfg.target),
        "expected_url": redact_url(expected),
        "observed_url": redact_url(observed_url),
    }


# -- DOM instrumentation ---------------------------------------------------
#
# Element-count cap: the loop bails out after MAX_ELEMENTS visible nodes and
# sets `truncated: true` on the returned payload. Without this cap, very large
# pages (e.g. infinite scrollers, content-management dumps) can produce
# multi-hundred-megabyte DOM JSON files and OOM the runner.

INSTRUMENT_JS = r"""
() => {
  const MAX_ELEMENTS = 5000;
  const MAX_TRAVERSED = 50000;
  const STYLE_PROPS = [
    'color', 'backgroundColor', 'backgroundImage',
    'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
    'textAlign', 'textTransform', 'textDecorationLine',
    'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
    'marginTop', 'marginRight', 'marginBottom', 'marginLeft',
    'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
    'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor',
    'borderTopLeftRadius', 'borderTopRightRadius',
    'borderBottomLeftRadius', 'borderBottomRightRadius',
    'outlineWidth', 'outlineColor', 'outlineStyle', 'outlineOffset',
    'boxShadow', 'opacity', 'cursor',
    'display', 'position', 'zIndex',
    'gap', 'rowGap', 'columnGap', 'overflow',
    'backdropFilter', 'webkitBackdropFilter',
  ];

  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'NOSCRIPT', 'TEMPLATE', 'HEAD']);

  function composedParent(el) {
    if (el.parentElement) return el.parentElement;
    const root = el.getRootNode ? el.getRootNode() : null;
    return root && root.host && root.host.nodeType === Node.ELEMENT_NODE ? root.host : null;
  }

  function isVisible(el, rect, cs) {
    if (rect.width === 0 || rect.height === 0) return false;
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (effectiveOpacity(el) <= 0) return false;
    return true;
  }

  function accessibleName(el) {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const ids = labelledBy.split(/\s+/);
      const text = ids.map(id => {
        const target = document.getElementById(id);
        return target ? target.textContent.trim() : '';
      }).join(' ').trim();
      if (text) return text;
    }
    if (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
      if (el.id) {
        const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lbl) return lbl.textContent.trim();
      }
      const wrappingLabel = el.closest('label');
      if (wrappingLabel) return wrappingLabel.textContent.trim();
    }
    if (el.tagName === 'IMG') return el.alt || '';
    if (el.tagName === 'BUTTON' || el.tagName === 'A') {
      const text = (el.textContent || '').trim();
      if (text) return text;
    }
    return '';
  }

  const TEXT_OWNER_SELECTOR = [
    'a', 'button', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'label', 'legend',
    'input', 'select', 'textarea', '[role="button"]', '[role="link"]',
    '[role="heading"]', '[role="tab"]', '[role="menuitem"]'
  ].join(',');

  function isPaintedTextNode(node) {
    if (!node.nodeValue || !node.nodeValue.trim() || !node.parentElement) return false;
    const parentStyles = getComputedStyle(node.parentElement);
    if (parentStyles.display === 'none' || parentStyles.visibility === 'hidden'
        || Number(parentStyles.opacity) === 0) return false;
    const parentRect = node.parentElement.getBoundingClientRect();
    if (parentRect.width <= 1 || parentRect.height <= 1) return false;
    if ((parentStyles.clip && parentStyles.clip !== 'auto')
        || (parentStyles.clipPath && parentStyles.clipPath !== 'none')) return false;
    const range = document.createRange();
    range.selectNodeContents(node);
    return Array.from(range.getClientRects()).some(rect => {
      let left = rect.left;
      let right = rect.right;
      let top = rect.top;
      let bottom = rect.bottom;
      let ancestor = node.parentElement;
      let ancestorDepth = 0;
      while (ancestor && ancestor !== document.documentElement && ancestorDepth < 128) {
        const styles = getComputedStyle(ancestor);
        const ancestorRect = ancestor.getBoundingClientRect();
        if (styles.overflowX !== 'visible') {
          left = Math.max(left, ancestorRect.left);
          right = Math.min(right, ancestorRect.right);
        }
        if (styles.overflowY !== 'visible') {
          top = Math.max(top, ancestorRect.top);
          bottom = Math.min(bottom, ancestorRect.bottom);
        }
        ancestor = composedParent(ancestor);
        ancestorDepth += 1;
      }
      return right - left > 1 && bottom - top > 1;
    });
  }

  function visibleTextInfo(el, elementColor) {
    if (el.tagName === 'INPUT'
        && ['button', 'reset', 'submit'].includes((el.type || '').toLowerCase())
        && (el.value || '').trim()) {
      return { hasVisibleText: true, textStyleDivergent: false };
    }
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const colors = new Set();
    let hasVisibleText = false;
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!isPaintedTextNode(node)) continue;
      const owner = node.parentElement.closest(TEXT_OWNER_SELECTOR);
      if (owner && owner !== el) continue;
      hasVisibleText = true;
      colors.add(getComputedStyle(node.parentElement).color);
    }
    return {
      hasVisibleText,
      textStyleDivergent: colors.size > 1 || (colors.size === 1 && !colors.has(elementColor)),
    };
  }

  function isInlineTextLink(el, hasVisibleText) {
    if (el.tagName !== 'A' || !hasVisibleText) return false;
    const container = el.closest('p, li, dd, dt, figcaption, blockquote, td, th');
    if (!container) return false;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (el.contains(node)) continue;
      if (isPaintedTextNode(node)) return true;
    }
    return false;
  }

  function hasHorizontalOverflowAncestor(el) {
    let parent = composedParent(el);
    let depth = 0;
    while (parent && parent !== document.body
        && parent !== document.documentElement && depth < 128) {
      const styles = getComputedStyle(parent);
      const clipsX = ['auto', 'scroll', 'hidden', 'clip'].includes(styles.overflowX);
      if (clipsX && parent.scrollWidth > parent.clientWidth + 1) return true;
      parent = composedParent(parent);
      depth += 1;
    }
    return false;
  }

  function pickStyles(el) {
    const cs = window.getComputedStyle(el);
    const out = {};
    for (const k of STYLE_PROPS) {
      const value = cs[k];
      out[k] = typeof value === 'string' ? value : '';
    }
    return [out, cs];
  }

  function effectiveOpacity(el) {
    let value = 1;
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      const opacity = Number(getComputedStyle(current).opacity);
      if (Number.isFinite(opacity)) value *= opacity;
      current = composedParent(current);
    }
    return Math.max(0, Math.min(1, value));
  }

  function effectiveAriaHidden(el) {
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      if (current.getAttribute('aria-hidden') === 'true') return true;
      current = composedParent(current);
    }
    return false;
  }

  function effectiveAriaDisabled(el) {
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      if (current.getAttribute('aria-disabled') === 'true') return true;
      current = composedParent(current);
    }
    return false;
  }

  // Scan stylesheets for :focus / :focus-visible rules. We strip the pseudo
  // and remember the bare selector so the analyzer can ask, for any element,
  // "does any user-authored focus rule actually match you?"
  function focusSelectors() {
    const focusable = [];
    let totalFocusRules = 0;
    let focusVisibleRules = 0;
    let inaccessibleStylesheets = 0;
    function visitRules(rules) {
      for (const rule of Array.from(rules || [])) {
        // CSSGroupingRule covers @media, @supports, and @layer. Recurse so
        // nested focus rules are not incorrectly reported as absent.
        if (rule.cssRules) visitRules(rule.cssRules);
        if (!rule.selectorText) continue;
        const sels = rule.selectorText.split(',').map(s => s.trim());
        for (const sel of sels) {
          if (sel.includes(':focus-visible')) {
            focusVisibleRules += 1;
            const stripped = sel.replace(/:focus-visible/g, '');
            if (stripped.trim()) focusable.push(stripped.trim());
          } else if (sel.includes(':focus')) {
            totalFocusRules += 1;
            const stripped = sel.replace(/:focus/g, '');
            if (stripped.trim()) focusable.push(stripped.trim());
          }
        }
      }
    }
    for (const sheet of Array.from(document.styleSheets)) {
      let rules;
      try { rules = sheet.cssRules; }
      catch (e) { inaccessibleStylesheets += 1; continue; /* cross-origin */ }
      if (!rules) continue;
      visitRules(rules);
    }
    return { selectors: focusable, totalFocusRules, focusVisibleRules, inaccessibleStylesheets };
  }

  function elementMatchesAnyFocusSelector(el, selectors) {
    for (const sel of selectors) {
      try {
        if (el.matches(sel)) return true;
      } catch (e) { /* invalid selector */ }
    }
    return false;
  }

  const focusInfo = focusSelectors();
  const focusedEl = document.activeElement && document.activeElement !== document.body
    ? document.activeElement : null;

  const results = [];
  // Walk the document including shadow roots. Without this, components that
  // live inside `<custom-element>` web components (e.g. design-system widgets
  // built with lit-html, Stencil, Polymer, or framework wrappers) are
  // invisible to querySelectorAll('*'). The walk is iterative to avoid stack
  // overflow on deeply-nested shadow trees, and bounded by MAX_ELEMENTS so a
  // pathological page cannot blow up the dump.
  function collectAll(root) {
    const out = [];
    const queue = [root];
    let traversalTruncated = false;
    while (queue.length) {
      const node = queue.shift();
      const list = node.querySelectorAll ? node.querySelectorAll('*') : [];
      for (const el of list) {
        out.push(el);
        if (out.length >= MAX_TRAVERSED) {
          traversalTruncated = true;
          return { elements: out, traversalTruncated };
        }
        if (el.shadowRoot) queue.push(el.shadowRoot);
      }
    }
    return { elements: out, traversalTruncated };
  }
  const collected = document.body
    ? collectAll(document.body)
    : { elements: [], traversalTruncated: false };
  const all = collected.elements;
  const indexMap = new Map();
  const visible = [];
  let nodesTraversed = 0;
  let visibleTruncated = false;
  for (const el of all) {
    nodesTraversed += 1;
    if (SKIP_TAGS.has(el.tagName)) continue;
    const rect = el.getBoundingClientRect();
    const [styles, cs] = pickStyles(el);
    if (!isVisible(el, rect, cs)) continue;
    indexMap.set(el, visible.length);
    visible.push({ el, rect, styles });
    if (visible.length >= MAX_ELEMENTS) { visibleTruncated = true; break; }
  }

  // Retain element identity without mutating page markup. Python uses this
  // array after the screenshot to ask Playwright for browser-computed ARIA
  // names, then deletes it before the page closes.
  try {
    Object.defineProperty(window, '__keenVisibleElements', {
      value: visible.map(entry => entry.el),
      configurable: true,
    });
  } catch (error) {
    window.__keenVisibleElements = visible.map(entry => entry.el);
  }

  let focusedIndex = -1;

  for (const entry of visible) {
    const { el, rect, styles } = entry;
    const textInfo = visibleTextInfo(el, styles.color);

    let parentIndex = -1;
    let p = composedParent(el);
    let parentDepth = 0;
    while (p && parentDepth < 128) {
      if (indexMap.has(p)) { parentIndex = indexMap.get(p); break; }
      p = composedParent(p);
      parentDepth += 1;
    }

    const directText = (el.childNodes.length === 1 && el.firstChild.nodeType === 3)
      ? (el.textContent || '').trim()
      : '';
    const semanticText = /^(H[1-6]|P|LABEL|LEGEND|FIGCAPTION)$/.test(el.tagName)
      ? (el.innerText || el.textContent || '').trim()
      : '';
    const text = (directText || semanticText).slice(0, 200);

    const hasFocusRule = focusInfo.selectors.length > 0
      && elementMatchesAnyFocusSelector(el, focusInfo.selectors);

    const idx = indexMap.get(el);
    if (el === focusedEl) focusedIndex = idx;

    results.push({
      index: idx,
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      classes: Array.from(el.classList),
      role: el.getAttribute('role') || null,
      type: el.getAttribute('type') || null,
      href: el.getAttribute('href') || null,
      ariaLabel: el.getAttribute('aria-label') || null,
      ariaHidden: el.getAttribute('aria-hidden') === 'true',
      effectiveAriaHidden: effectiveAriaHidden(el),
      ariaDisabled: el.getAttribute('aria-disabled') === 'true',
      effectiveAriaDisabled: effectiveAriaDisabled(el),
      ariaModal: el.getAttribute('aria-modal') === 'true',
      ariaCurrent: el.getAttribute('aria-current') || null,
      hasAlt: el.tagName === 'IMG' ? el.hasAttribute('alt') : null,
      autocomplete: el.getAttribute('autocomplete') || null,
      inputmode: el.getAttribute('inputmode') || null,
      required: el.required || el.getAttribute('aria-required') === 'true' || false,
      disabled: Boolean(el.disabled || (el.matches && el.matches(':disabled'))),
      tabIndex: el.tabIndex,
      name: accessibleName(el),
      nameSource: 'dom-heuristic',
      placeholder: el.getAttribute('placeholder') || null,
      text,
      hasVisibleText: textInfo.hasVisibleText,
      textStyleDivergent: textInfo.textStyleDivergent,
      isInlineTextLink: isInlineTextLink(el, textInfo.hasVisibleText),
      hasHorizontalOverflowAncestor: hasHorizontalOverflowAncestor(el),
      box: {
        x: Math.round(rect.x + window.scrollX),
        y: Math.round(rect.y + window.scrollY),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      },
      styles,
      effectiveOpacity: effectiveOpacity(el),
      parentIndex,
      hasUserFocusRule: hasFocusRule,
    });
  }

  return {
    url: window.location.href,
    title: document.title,
    devicePixelRatio: window.devicePixelRatio,
    documentBackgroundColor: (() => {
      const values = [
        document.body ? getComputedStyle(document.body).backgroundColor : '',
        getComputedStyle(document.documentElement).backgroundColor,
      ];
      return values.find(value =>
        value
        && value !== 'transparent'
        && !/^rgba\([^)]*,\s*0(?:\.0+)?\s*\)$/i.test(value)
      ) || 'rgb(255, 255, 255)';
    })(),
    documentSize: {
      width: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
    },
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
    },
    focus_coverage: {
      total_focus_rules: focusInfo.totalFocusRules,
      focus_visible_rules: focusInfo.focusVisibleRules,
      inaccessible_stylesheets: focusInfo.inaccessibleStylesheets,
      evidence_complete: focusInfo.inaccessibleStylesheets === 0,
    },
    focused_index: focusedIndex,
    surface: (() => {
      const body = document.body;
      const root = document.documentElement;
      const text = body ? (body.innerText || '').trim() : '';
      const paintedBackground = [body, root].some(element => {
        if (!element) return false;
        const style = getComputedStyle(element);
        const image = style.backgroundImage;
        const color = style.backgroundColor;
        return (image && image !== 'none')
          || (color
            && color !== 'transparent'
            && !/^rgba\([^)]*,\s*0(?:\.0+)?\s*\)$/i.test(color));
      });
      const pseudoContent = body && ['::before', '::after'].some(pseudo => {
        const style = getComputedStyle(body, pseudo);
        const content = style.content;
        return style.display !== 'none'
          && content
          && !['none', 'normal', '""', "''"].includes(content);
      });
      return {
        body_text_chars: text.length,
        painted_background: paintedBackground,
        pseudo_content: Boolean(pseudoContent),
      };
    })(),
    elements: results,
    truncated: visibleTruncated || collected.traversalTruncated,
    coverage: {
      complete: !(visibleTruncated || collected.traversalTruncated),
      nodes_traversed: nodesTraversed,
      visible_nodes: results.length,
      max_visible_nodes: MAX_ELEMENTS,
      max_traversed_nodes: MAX_TRAVERSED,
      reason: visibleTruncated
        ? 'visible-node-cap'
        : (collected.traversalTruncated ? 'traversal-cap' : null),
    },
  };
}
"""


# -- Filename helpers ------------------------------------------------------


def _slug(s: str) -> str:
    parsed = urlparse(s)
    base = (parsed.path or "/").strip("/")
    if not base:
        base = parsed.netloc or "root"
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-").lower()
    base = (base or "page")[:100].rstrip(".")
    if base.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
        r"(?:COM|LPT)[1-9]",
        base,
        flags=re.IGNORECASE,
    ):
        base = f"page-{base}"
    return base or "page"


# -- Auth: declarative JSON DSL --------------------------------------------
#
# The preferred path. A JSON file shaped like:
#
#   {
#     "steps": [
#       {"action": "goto", "url": "https://example.com/login"},
#       {"action": "wait_for_selector", "selector": "#email"},
#       {"action": "fill", "selector": "#email",    "value_env": "EMAIL"},
#       {"action": "fill", "selector": "#password", "value_env": "PASSWORD"},
#       {"action": "click", "selector": "button[type=submit]"},
#       {"action": "wait_for_url", "pattern": "**/dashboard"}
#     ]
#   }
#
# Schema is validated up front; actions are dispatched through a closed
# whitelist so a malicious or buggy JSON file cannot smuggle in code paths.

_ALLOWED_AUTH_ACTIONS: frozenset[str] = frozenset(
    {
        "goto",
        "wait_for_selector",
        "wait_for_url",
        "fill",
        "set_storage",
        "set_cookie",
        "click",
        "press",
        "check",
        "uncheck",
        "select_option",
        "eval_safe",
        "sleep_ms",
    }
)

_ALLOWED_WAIT_STATES: frozenset[str] = frozenset(
    {
        "visible",
        "attached",
        "hidden",
        "detached",
    }
)

_ALLOWED_CLICK_BUTTONS: frozenset[str] = frozenset({"left", "right", "middle"})

_SELECTOR_MAX_LEN = 512
_SLEEP_MS_MAX = 5000
# Hardening: cap auth-steps JSON file size so a hostile file can't blow up
# json.loads with a multi-GB payload. 256 KiB is more than enough for any
# realistic login flow (the realistic_login_steps fixture is < 1 KiB).
_AUTH_STEPS_MAX_BYTES = 262_144
# Cap timeout_ms for wait_for_url so a never-matching pattern can't block
# indefinitely. The default is 15 s; explicit user value can extend up to
# this hard ceiling.
_WAIT_TIMEOUT_MAX_MS = 60_000
_WAIT_TIMEOUT_DEFAULT_MS = 15_000
_TIMED_AUTH_ACTIONS = frozenset(
    {
        "goto",
        "wait_for_selector",
        "wait_for_url",
        "fill",
        "click",
        "press",
        "check",
        "uncheck",
        "select_option",
    }
)

# Press-key whitelist. Playwright accepts free-form key chords like
# `Control+A`, which means an attacker-controlled JSON could deliver
# OS-level shortcuts (Meta+Q to quit, Control+W to close tab, etc).
# Closed regex covers ANSI letters, digits, F1..F12, named keys, and
# modified chords like `Control+Shift+I`.
_PRESS_KEY_RE = re.compile(
    r"^("
    r"[A-Za-z0-9]"
    r"|F[1-9]|F1[0-2]"
    r"|Tab|Enter|Escape|Backspace|Delete"
    r"|ArrowUp|ArrowDown|ArrowLeft|ArrowRight"
    r"|Home|End|PageUp|PageDown|Space"
    r"|(Control|Alt|Shift|Meta)\+[A-Za-z0-9+]+"
    r")$"
)

# Env-var name hygiene. `fill.value_env` lets a JSON file pick which
# environment variable's value gets typed into the form field. Without
# guards, a hostile file could read `PATH` or `LD_PRELOAD` and exfiltrate
# host config by submitting it to an attacker-controlled login form.
# Hardening:
#   1. Name must match [A-Z][A-Z0-9_]* (POSIX env-var grammar).
#   2. Name must start with KEEN_ unless the caller explicitly opts
#      out via allow_unprefixed_env=True (no caller currently does).
#   3. Hard blocklist of ambient/sensitive names even if rule 2 is relaxed.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_AUTH_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ENV_NAME_PREFIX = "KEEN_"
_BLOCKED_ENV_NAMES: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "PWD",
        "OLDPWD",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
    }
)

# eval_safe value/key character class. Cookie values and storage values
# show up in HTTP headers and DOM — restrict them to a printable, mostly
# token-friendly set. Anything outside this class (newlines, NUL, quotes
# already excluded by the regex) is blocked.
_EVAL_SAFE_TOTAL_MAX_LEN = 1024


# Whitelisted eval_safe expression forms. Each pattern captures the literal
# value(s) we need to pass through to Playwright as parameters (NEVER as
# interpolated JS strings).
#
# Allowed:
#   document.cookie = '...'
#   localStorage.setItem('k', 'v')
#   localStorage.removeItem('k')
#   sessionStorage.setItem('k', 'v')
#   sessionStorage.removeItem('k')

_RE_COOKIE = re.compile(r"^\s*document\.cookie\s*=\s*'((?:[^'\\]|\\.)*)'\s*;?\s*$")
_RE_STORAGE_SET = re.compile(
    r"^\s*(localStorage|sessionStorage)\.setItem\(\s*"
    r"'((?:[^'\\]|\\.)*)'\s*,\s*"
    r"'((?:[^'\\]|\\.)*)'\s*\)\s*;?\s*$"
)
_RE_STORAGE_REMOVE = re.compile(
    r"^\s*(localStorage|sessionStorage)\.removeItem\(\s*"
    r"'((?:[^'\\]|\\.)*)'\s*\)\s*;?\s*$"
)


def _validate_selector(selector: Any, *, where: str) -> str:
    """Selector hardening — string, length-capped, no template-literal injection."""
    if not isinstance(selector, str):
        raise ValueError(f"{where}: selector must be a string")
    if not (1 <= len(selector) <= _SELECTOR_MAX_LEN):
        raise ValueError(
            f"{where}: selector length must be 1..{_SELECTOR_MAX_LEN}, got {len(selector)}"
        )
    # Backtick template literals + ${...} interpolation can sneak JS exec into
    # locator strings if any downstream code ever falls back to eval. Reject.
    if "`" in selector:
        raise ValueError(
            f"{where}: selector contains backtick (template literal injection blocked)"
        )
    if "${" in selector:
        raise ValueError(f"{where}: selector contains ${{}} (interpolation blocked)")
    return selector


def _parse_eval_safe(expr: Any) -> tuple[str, tuple[Any, ...]]:
    """Match a narrow expression form and return (kind, args_tuple).

    Returns one of:
      ("cookie", (value,))
      ("localStorage.set",   (key, value))
      ("localStorage.remove",(key,))
      ("sessionStorage.set",   (key, value))
      ("sessionStorage.remove",(key,))

    Raises ValueError if the expression is not in the whitelist.

    The regex patterns are anchored to ``^…$`` so trailing-content tricks
    like ``document.cookie = 'a'; alert(1); //'`` cannot slip past on the
    OK prefix. The quoted-string class ``[^'\\]|\\.`` also disallows
    unescaped single quotes inside the literal.
    """
    if not isinstance(expr, str):
        raise ValueError("eval_safe: expr must be a string")
    # Cap total length conservatively. Storage values *can* be larger in
    # theory but for an auth bootstrap there is no realistic need.
    if len(expr) > _EVAL_SAFE_TOTAL_MAX_LEN:
        raise ValueError(f"eval_safe: expr too long (>{_EVAL_SAFE_TOTAL_MAX_LEN} chars)")

    m = _RE_COOKIE.match(expr)
    if m:
        return ("cookie", (m.group(1),))

    m = _RE_STORAGE_SET.match(expr)
    if m:
        storage, key, val = m.group(1), m.group(2), m.group(3)
        return (f"{storage}.set", (key, val))

    m = _RE_STORAGE_REMOVE.match(expr)
    if m:
        storage, key = m.group(1), m.group(2)
        return (f"{storage}.remove", (key,))

    raise ValueError(
        "eval_safe: expression not in whitelist. Allowed forms: "
        "document.cookie = '...'; localStorage.setItem('k','v'); "
        "localStorage.removeItem('k'); sessionStorage.setItem('k','v'); "
        "sessionStorage.removeItem('k')"
    )


def _validate_env_name(name: Any, *, where: str, allow_unprefixed: bool = False) -> str:
    """Validate an environment variable name for fill.value_env lookups.

    Rules:
      * Must be a non-empty string matching ``[A-Z][A-Z0-9_]*``.
      * Must not be in the hardcoded blocklist (PATH, HOME, LD_PRELOAD, …).
      * Unless ``allow_unprefixed=True``, must start with ``KEEN_``
        — this is the secure default and matches what real auth flows do.

    Raises ValueError on any violation. Returns the name on success.
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"{where}: value_env must be a non-empty string")
    if not _ENV_NAME_RE.match(name):
        raise ValueError(f"{where}: value_env {name!r} must match [A-Z][A-Z0-9_]*")
    if name in _BLOCKED_ENV_NAMES:
        raise ValueError(
            f"{where}: value_env {name!r} is in the blocked-env list "
            f"(ambient/sensitive: PATH, HOME, LD_PRELOAD, DYLD_*, etc.)"
        )
    if not allow_unprefixed and not name.startswith(_ENV_NAME_PREFIX):
        raise ValueError(
            f"{where}: value_env {name!r} must start with "
            f"{_ENV_NAME_PREFIX!r} (defense against ambient-var exfil). "
            "Set KEEN_<NAME> in your environment instead."
        )
    return name


def _validate_auth_key(value: Any, *, where: str) -> str:
    """Validate a storage key or cookie name used by structured auth actions."""
    if not isinstance(value, str) or not _AUTH_KEY_RE.fullmatch(value):
        raise ValueError(
            f"{where}: must be 1-128 letters, numbers, dots, colons, underscores, or hyphens"
        )
    return value


def _validate_press_key(key: Any, *, where: str) -> str:
    """Restrict press(key=…) to a closed regex.

    Playwright's ``page.press(selector, key)`` accepts chords like
    ``Control+A``. Without this guard, a hostile JSON could dispatch
    OS-level shortcuts (Meta+Q to quit Chromium, Control+W to close the
    tab, etc.) and disrupt the capture. The regex covers ANSI letters,
    digits, F1..F12, common named keys, and explicit modifier+key chords.
    """
    if not isinstance(key, str) or not key:
        raise ValueError(f"{where}: 'press' requires non-empty 'key' string")
    if len(key) > 64:
        raise ValueError(f"{where}: 'press.key' too long")
    if not _PRESS_KEY_RE.match(key):
        raise ValueError(
            f"{where}: 'press.key' {key!r} not in allowed key set "
            "(letters, digits, F1..F12, Tab, Enter, Escape, Arrow*, "
            "Home/End/Page*/Space, or Control|Alt|Shift|Meta + key)"
        )
    return key


def validate_auth_steps(
    data: Any,
    *,
    allow_internal: bool = False,
    allow_file: bool = False,
) -> list[dict[str, Any]]:
    """Validate the top-level shape and each step. Returns the steps list.

    Schema:
      top-level: {"steps": [...]}
      each step: a dict with required "action" key whose value is in the whitelist.

    Per-action requirements:
      goto:              "url" (str)
      wait_for_selector: "selector"
      wait_for_url:      "pattern" (str)
      fill:              "selector" + ("value" XOR "value_env"), env name must be a str
      set_storage:       "key" + "value_env" (localStorage, parameter-bound)
      set_cookie:        "name" + "value_env" (scoped to the current page URL)
      click:             "selector", optional "button" in {"left","right","middle"}
      press:             "selector", "key" (str)
      check / uncheck:   "selector"
      select_option:     "selector", "value" (str) OR "values" (list[str])
      eval_safe:         "expr" matching the whitelist regex
      sleep_ms:          "ms" int 0..5000

    Raises ValueError on any violation (with the offending step index in scope).
    """
    if not isinstance(data, dict):
        raise ValueError("auth-steps JSON: top-level must be an object")
    if "steps" not in data:
        raise ValueError("auth-steps JSON: missing required key 'steps'")
    steps = data["steps"]
    if not isinstance(steps, list):
        raise ValueError("auth-steps JSON: 'steps' must be a list")

    validated: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        where = f"step {i}"
        if not isinstance(step, dict):
            raise ValueError(f"{where}: must be an object")
        if "action" not in step:
            raise ValueError(f"{where}: missing required key 'action'")
        action = step["action"]
        if not isinstance(action, str):
            raise ValueError(f"{where}: 'action' must be a string")
        if action not in _ALLOWED_AUTH_ACTIONS:
            raise ValueError(
                f"{where}: unknown action {action!r}. Supported: {sorted(_ALLOWED_AUTH_ACTIONS)}"
            )

        # Per-action validation.
        if action == "goto":
            url = step.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError(f"{where}: 'goto' requires non-empty 'url' string")
            # SSRF guard — same gate as the top-level target. Without this
            # a malicious auth-steps JSON could redirect the page to
            # http://169.254.169.254/ (cloud metadata) and then
            # wait_for_url to capture credentials. Private/file targets remain
            # denied by default. The caller may pass an explicit policy only
            # after the corresponding CLI permission was granted.
            try:
                validate_target(
                    url,
                    allow_internal=allow_internal,
                    allow_file=allow_file,
                )
            except ValueError as e:
                raise ValueError(f"{where}: 'goto' url rejected: {e}") from e
        elif action == "wait_for_selector":
            _validate_selector(step.get("selector"), where=where)
            state = step.get("state")
            if state is not None and state not in _ALLOWED_WAIT_STATES:
                raise ValueError(f"{where}: 'state' must be one of {sorted(_ALLOWED_WAIT_STATES)}")
        elif action == "wait_for_url":
            pattern = step.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"{where}: 'wait_for_url' requires non-empty 'pattern' string")
            if "://" in pattern:
                parsed_pattern = urlparse(pattern)
                if "*" in parsed_pattern.scheme or "*" in parsed_pattern.netloc:
                    raise ValueError(
                        f"{where}: 'wait_for_url' wildcards are allowed only in the path"
                    )
            # Cap the wait so a never-matching pattern can't block
            # indefinitely. timeout_ms (if present) is also validated
            # against _WAIT_TIMEOUT_MAX_MS below at the top-level
            # timeout check, but be explicit here to surface the cap.
        elif action == "fill":
            _validate_selector(step.get("selector"), where=where)
            has_value = "value" in step
            has_value_env = "value_env" in step
            if has_value and has_value_env:
                raise ValueError(
                    f"{where}: 'fill' must specify exactly one of 'value' or 'value_env'"
                )
            if not has_value and not has_value_env:
                raise ValueError(f"{where}: 'fill' requires 'value' or 'value_env'")
            if has_value and not isinstance(step["value"], str):
                raise ValueError(f"{where}: 'fill.value' must be a string")
            if has_value_env:
                _validate_env_name(step["value_env"], where=f"{where}: fill")
        elif action == "set_storage":
            _validate_auth_key(step.get("key"), where=f"{where}: set_storage.key")
            _validate_env_name(step.get("value_env"), where=f"{where}: set_storage")
        elif action == "set_cookie":
            _validate_auth_key(step.get("name"), where=f"{where}: set_cookie.name")
            _validate_env_name(step.get("value_env"), where=f"{where}: set_cookie")
        elif action == "click":
            _validate_selector(step.get("selector"), where=where)
            btn = step.get("button")
            if btn is not None and btn not in _ALLOWED_CLICK_BUTTONS:
                raise ValueError(
                    f"{where}: 'button' must be one of {sorted(_ALLOWED_CLICK_BUTTONS)}"
                )
        elif action == "press":
            _validate_selector(step.get("selector"), where=where)
            _validate_press_key(step.get("key"), where=where)
        elif action in ("check", "uncheck"):
            _validate_selector(step.get("selector"), where=where)
        elif action == "select_option":
            _validate_selector(step.get("selector"), where=where)
            has_value = "value" in step
            has_values = "values" in step
            if has_value == has_values:
                raise ValueError(
                    f"{where}: 'select_option' requires exactly one of 'value' or 'values'"
                )
            if has_value and not isinstance(step["value"], str):
                raise ValueError(f"{where}: 'select_option.value' must be a string")
            if has_values:
                vs = step["values"]
                if not isinstance(vs, list) or not all(isinstance(v, str) for v in vs):
                    raise ValueError(f"{where}: 'select_option.values' must be a list of strings")
        elif action == "eval_safe":
            _parse_eval_safe(step.get("expr"))
        elif action == "sleep_ms":
            ms = step.get("ms")
            # bool is a subclass of int — reject it explicitly.
            if not isinstance(ms, int) or isinstance(ms, bool):
                raise ValueError(f"{where}: 'sleep_ms.ms' must be an int")
            if ms < 0:
                raise ValueError(f"{where}: 'sleep_ms.ms' must be >= 0")
            if ms > _SLEEP_MS_MAX:
                raise ValueError(
                    f"{where}: 'sleep_ms.ms' exceeds cap of {_SLEEP_MS_MAX}ms (got {ms})"
                )

        # Optional per-step timeout (applies to actions that take one).
        timeout = step.get("timeout_ms")
        if timeout is not None:
            if action not in _TIMED_AUTH_ACTIONS:
                raise ValueError(f"{where}: 'timeout_ms' is not valid for action {action!r}")
            if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
                raise ValueError(f"{where}: 'timeout_ms' must be a positive int")
            if timeout > _WAIT_TIMEOUT_MAX_MS:
                raise ValueError(
                    f"{where}: '{action}.timeout_ms' exceeds cap of "
                    f"{_WAIT_TIMEOUT_MAX_MS}ms (got {timeout})"
                )

        validated.append(step)

    return validated


def _env_required(name: str, *, where: str) -> str:
    """Fetch a required env var. Raise ValueError on miss; never log the value.

    Defense in depth: ``_validate_env_name`` already ran at schema-validation
    time, but re-validate here so a future call site that hands a raw name
    can't bypass the blocklist. Cheap, deterministic, no env touched on the
    bad path.
    """
    _validate_env_name(name, where=where)
    try:
        return os.environ[name]
    except KeyError as e:
        raise ValueError(f"env var {name} not set for {where}") from e


def load_and_validate_auth_steps(
    steps_path: Path,
    *,
    allow_internal: bool = False,
    allow_file: bool = False,
) -> list[dict[str, Any]]:
    """Load + size-cap + parse + schema-validate the auth-steps JSON.

    Split out from ``apply_auth_steps`` so callers can fail fast BEFORE
    launching Playwright. Failing here on schema/SSRF/file-size catches
    bad input in milliseconds, vs. seconds spent spinning up a browser
    just to reject the same JSON.

    Hardening:
      * File size cap (256 KiB) catches gigabyte-JSON DoS.
      * A single bounded binary read avoids a stat/read race and never loads
        more than one byte beyond the cap.
      * ``validate_auth_steps`` enforces the schema + SSRF guard on
        ``goto`` URLs.
    """
    if not steps_path.exists():
        raise FileNotFoundError(f"auth-steps file not found: {steps_path}")
    if steps_path.is_symlink():
        raise ValueError(f"auth-steps file must not be a symlink: {steps_path}")
    try:
        with steps_path.open("rb") as handle:
            raw = handle.read(_AUTH_STEPS_MAX_BYTES + 1)
    except OSError as e:
        raise ValueError(f"auth-steps file read failed for {steps_path}: {e}") from e
    if len(raw) > _AUTH_STEPS_MAX_BYTES:
        raise ValueError(
            f"auth-steps file too large: exceeds cap of "
            f"{_AUTH_STEPS_MAX_BYTES} bytes (256 KiB). Trim the JSON or "
            "raise the cap if your login flow genuinely needs that much."
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"auth-steps JSON parse error in {steps_path}: {e}") from e
    return validate_auth_steps(
        data,
        allow_internal=allow_internal,
        allow_file=allow_file,
    )


def _validate_auth_environment(steps: list[dict[str, Any]]) -> None:
    """Fail before browser launch when a declared credential variable is absent."""
    for index, step in enumerate(steps):
        name = step.get("value_env")
        if isinstance(name, str) and name not in os.environ:
            raise ValueError(f"env var {name} not set for auth step {index}")


async def apply_auth_steps(
    page: Page,
    steps_path: Path,
    *,
    allow_internal: bool = False,
    allow_file: bool = False,
) -> None:
    """Run the declarative auth steps in `steps_path` against `page`.

    Args:
        page: Playwright async Page (browser tab).
        steps_path: path to a JSON file shaped `{"steps": [...]}`.

    Raises:
        FileNotFoundError: if the path does not exist.
        ValueError: if the JSON is malformed or any step fails schema validation.
        Playwright errors: bubble up from individual step execution.

    Never executes user-provided code; every action is mapped through a
    closed dispatch table and parameters are passed via Playwright's
    arg-binding form (page.evaluate(expr, arg)).
    """
    steps = load_and_validate_auth_steps(
        steps_path,
        allow_internal=allow_internal,
        allow_file=allow_file,
    )
    n = len(steps)

    def step_timeout(step: dict[str, Any], default: int) -> int:
        requested = int(step.get("timeout_ms", default))
        return max(1, min(requested, _WAIT_TIMEOUT_MAX_MS))

    for i, step in enumerate(steps, start=1):
        action = step["action"]
        # Defense in depth: re-check that the action is in the closed set.
        # Validator already did this, but if a future refactor accidentally
        # widens the validator without updating dispatch, fail closed.
        if action not in _ALLOWED_AUTH_ACTIONS:
            raise ValueError(
                f"step {i}: action {action!r} not in closed dispatch set "
                f"(allowed: {sorted(_ALLOWED_AUTH_ACTIONS)})"
            )
        # Logging: never include env var values; for fill steps log the
        # selector and the env var *name* only.
        if action == "goto":
            # Re-validate at apply time. The schema gate already ran in
            # load_and_validate_auth_steps, but DNS results can change
            # between validation and execution (TOCTOU). Re-resolving the
            # host here closes that small window and matches the SSRF
            # pattern used by validate_target at the top-level target.
            validate_target(
                step["url"],
                allow_internal=allow_internal,
                allow_file=allow_file,
            )
            logger.info("auth step %d/%d: goto %s", i, n, redact_url(step["url"]))
            await page.goto(
                step["url"],
                timeout=step_timeout(step, 30_000),
                wait_until="domcontentloaded",
            )
        elif action == "wait_for_selector":
            sel = step["selector"]
            logger.info("auth step %d/%d: wait_for_selector %s", i, n, sel)
            kwargs: dict[str, Any] = {"timeout": step_timeout(step, _WAIT_TIMEOUT_DEFAULT_MS)}
            if step.get("state"):
                kwargs["state"] = step["state"]
            await page.wait_for_selector(sel, **kwargs)
        elif action == "wait_for_url":
            pattern = step["pattern"]
            # Cap the wait. Validator already enforces the ceiling on
            # explicit timeout_ms, but clamp here too for belt + braces.
            timeout_ms = step_timeout(step, _WAIT_TIMEOUT_DEFAULT_MS)
            logger.info(
                "auth step %d/%d: wait_for_url %s (timeout=%dms)",
                i,
                n,
                pattern,
                timeout_ms,
            )
            await page.wait_for_url(pattern, timeout=timeout_ms)
        elif action == "fill":
            sel = step["selector"]
            if "value_env" in step:
                env_name = step["value_env"]
                value = _env_required(env_name, where=f"fill step {i}")
                logger.info("auth step %d/%d: fill %s (from env %s)", i, n, sel, env_name)
            else:
                value = step["value"]
                logger.warning(
                    "auth step %d/%d: fill %s using literal value "
                    "(prefer value_env for credentials)",
                    i,
                    n,
                    sel,
                )
            await page.fill(sel, value, timeout=step_timeout(step, 15_000))
        elif action == "set_storage":
            env_name = step["value_env"]
            value = _env_required(env_name, where=f"set_storage step {i}")
            logger.info(
                "auth step %d/%d: set_storage %s (from env %s)",
                i,
                n,
                step["key"],
                env_name,
            )
            await page.evaluate(
                "([key, value]) => { window.localStorage.setItem(key, value); }",
                [step["key"], value],
            )
        elif action == "set_cookie":
            env_name = step["value_env"]
            value = _env_required(env_name, where=f"set_cookie step {i}")
            current_url = str(page.url)
            validate_target(
                current_url,
                allow_internal=allow_internal,
                allow_file=allow_file,
            )
            logger.info(
                "auth step %d/%d: set_cookie %s (from env %s)",
                i,
                n,
                step["name"],
                env_name,
            )
            await page.context.add_cookies(
                [{"name": step["name"], "value": value, "url": current_url}]
            )
        elif action == "click":
            sel = step["selector"]
            logger.info("auth step %d/%d: click %s", i, n, sel)
            kwargs = {"timeout": step_timeout(step, 15_000)}
            if step.get("button"):
                kwargs["button"] = step["button"]
            await page.click(sel, **kwargs)
        elif action == "press":
            sel = step["selector"]
            key = step["key"]
            logger.info("auth step %d/%d: press %s on %s", i, n, key, sel)
            await page.press(sel, key, timeout=step_timeout(step, 15_000))
        elif action == "check":
            sel = step["selector"]
            logger.info("auth step %d/%d: check %s", i, n, sel)
            await page.check(sel, timeout=step_timeout(step, 15_000))
        elif action == "uncheck":
            sel = step["selector"]
            logger.info("auth step %d/%d: uncheck %s", i, n, sel)
            await page.uncheck(sel, timeout=step_timeout(step, 15_000))
        elif action == "select_option":
            sel = step["selector"]
            logger.info("auth step %d/%d: select_option %s", i, n, sel)
            if "values" in step:
                await page.select_option(
                    sel,
                    step["values"],
                    timeout=step_timeout(step, 15_000),
                )
            else:
                await page.select_option(
                    sel,
                    step["value"],
                    timeout=step_timeout(step, 15_000),
                )
        elif action == "eval_safe":
            kind, args = _parse_eval_safe(step["expr"])
            logger.info("auth step %d/%d: eval_safe (%s)", i, n, kind)
            await _exec_eval_safe(page, kind, args)
        elif action == "sleep_ms":
            ms = int(step["ms"])
            # Defense in depth: clamp to [0, _SLEEP_MS_MAX] even though
            # validate_auth_steps already enforced this.
            ms = max(0, min(ms, _SLEEP_MS_MAX))
            logger.info("auth step %d/%d: sleep_ms %d", i, n, ms)
            await page.wait_for_timeout(ms)
        else:  # pragma: no cover — validator already rejected unknown actions
            raise ValueError(f"step {i}: unhandled action {action!r}")


async def _exec_eval_safe(page: Page, kind: str, args: tuple[Any, ...]) -> None:
    """Dispatch a pre-parsed eval_safe expression.

    Every call uses Playwright's parameter-binding form
    `await page.evaluate(jsExpr, arg)` so the raw string is never
    interpolated into JS source. The JS bodies are constants.
    """
    if kind == "cookie":
        (value,) = args
        # JS receives the value as the function argument; no interpolation.
        await page.evaluate("(v) => { document.cookie = v; }", value)
        return
    if kind == "localStorage.set":
        key, value = args
        await page.evaluate(
            "(kv) => { localStorage.setItem(kv[0], kv[1]); }",
            [key, value],
        )
        return
    if kind == "localStorage.remove":
        (key,) = args
        await page.evaluate("(k) => { localStorage.removeItem(k); }", key)
        return
    if kind == "sessionStorage.set":
        key, value = args
        await page.evaluate(
            "(kv) => { sessionStorage.setItem(kv[0], kv[1]); }",
            [key, value],
        )
        return
    if kind == "sessionStorage.remove":
        (key,) = args
        await page.evaluate("(k) => { sessionStorage.removeItem(k); }", key)
        return
    raise ValueError(f"eval_safe: unknown kind {kind!r}")


# -- Auth: script escape hatch (UNSAFE) ------------------------------------


async def _maybe_login(page: Page, auth_script: Path | None) -> None:
    """Load and run a user-supplied auth script (ESCAPE HATCH — UNSAFE).

    Gated by the CLI: callers must pass `--unsafe-auth-script` to enable, and
    by default the script must live under cwd. This function trusts that the
    CLI has done that vetting; it is *not* the security boundary.

    Prefer `apply_auth_steps` (declarative JSON DSL) for any login that fits
    the supported action whitelist.
    """
    if auth_script is None:
        return
    spec = importlib.util.spec_from_file_location("keen_auth", auth_script)
    if not spec or not spec.loader:
        raise RuntimeError(f"could not load auth script: {auth_script}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "login"):
        raise RuntimeError(f"{auth_script} must define `async def login(page)`")
    await mod.login(page)


# -- States DSL ------------------------------------------------------------

# Each setup step is a dict: {"action": <name>, ...args}. Actions that target a
# selector accept "fallback" as a string:
#   "first"         — first match for the same selector (no-op if none)
#   "first-button"  — first <button> on the page
#   "first-input"   — first focusable form control

_FALLBACK_SELECTORS = {
    "first": None,
    "first-button": "button",
    "first-input": "input:not([type=hidden]), textarea, select",
}


async def _resolve_locator(page, selector: str, fallback: str | None):
    """Return a Playwright locator for the selector, or for the fallback if missing."""
    if selector:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                return loc
        except (ValueError, TypeError) as e:
            logger.warning(
                "locator lookup for selector '%s' failed: %s",
                selector,
                type(e).__name__,
            )
        except Exception as e:
            logger.warning(
                "locator lookup for selector '%s' missed: %s",
                selector,
                type(e).__name__,
            )
    if fallback and fallback in _FALLBACK_SELECTORS and _FALLBACK_SELECTORS[fallback]:
        try:
            loc = page.locator(_FALLBACK_SELECTORS[fallback]).first
            if await loc.count() > 0:
                return loc
        except Exception as e:
            logger.warning(
                "fallback locator '%s' missed for selector '%s': %s",
                fallback,
                selector,
                e,
            )
            return None
    return None


async def _apply_setup_step(_context, page, step: dict[str, Any]) -> None:
    action = step.get("action")
    if not action:
        return

    if action in ("hover", "focus", "click"):
        selector = step.get("selector", "")
        loc = await _resolve_locator(page, selector, step.get("fallback"))
        if loc is None:
            return
        try:
            if action == "hover":
                await loc.hover(timeout=2000)
            elif action == "focus":
                await loc.focus(timeout=2000)
            elif action == "click":
                await loc.click(timeout=2000)
        except Exception as e:
            logger.warning(
                "%s on selector '%s' missed: %s",
                action,
                selector,
                e,
            )
            return
        return

    if action == "emulate_color_scheme":
        await page.emulate_media(color_scheme=step.get("value", "light"))
        return
    if action == "emulate_forced_colors":
        await page.emulate_media(forced_colors=step.get("value", "none"))
        return
    if action == "emulate_reduced_motion":
        await page.emulate_media(reduced_motion=step.get("value", "no-preference"))
        return
    if action == "set_zoom":
        z = float(step.get("value", 1.0))
        if z != z or z < 1.0 or z > 5.0:
            logger.warning("set_zoom ignored invalid factor: %r", z)
            return
        viewport = page.viewport_size
        if not viewport:
            logger.warning("set_zoom skipped because the page has no fixed viewport")
            return
        # Browser zoom reduces the CSS-pixel layout viewport and therefore
        # activates responsive breakpoints. CSS zoom merely magnifies the
        # existing desktop layout, creating false horizontal-overflow results.
        await page.set_viewport_size(
            {
                "width": max(1, round(float(viewport["width"]) / z)),
                "height": max(1, round(float(viewport["height"]) / z)),
            }
        )
        return

    # Unknown action: silently ignore so future actions don't crash old runs.


# -- Capture ---------------------------------------------------------------


async def _install_ssrf_route_guard(
    context,
    *,
    allow_internal: bool,
    allow_file: bool,
    file_roots: tuple[Path, ...],
    file_documents: tuple[Path, ...],
    internal_origins: tuple[NetworkOrigin, ...],
) -> None:
    """Register a per-context route handler that re-validates every request.

    Pre-flight `validate_target` only checks the initial URL once. The route
    guard adds coverage for:

        1. DNS changes that Python's request-time resolution observes.
        2. Redirect chains — `http://attacker.com/r` -> 302 ->
           `http://169.254.169.254/`. Playwright follows the redirect and
           fetches the metadata endpoint without us ever re-checking.
        3. Sub-resources — a benign-looking page <iframe>s or <img>s a
           private URL.

    The route handler runs on every request the page makes. It cannot pin
    Chromium's DNS answer to Python's separately resolved address, so it
    mitigates but does not fully eliminate same-request DNS rebinding. Aborted
    requests show up to the page as net::FAILED, which `_goto_with_retries`
    already treats as retryable/reportable.
    """

    async def _on_request(route) -> None:
        url = route.request.url
        try:
            revalidate_target_at_request_time(
                url,
                allow_internal=allow_internal,
                allow_file=allow_file,
                file_roots=file_roots,
                file_documents=file_documents,
                resource_type=route.request.resource_type,
                is_navigation_request=route.request.is_navigation_request(),
                internal_origins=internal_origins,
            )
        except ValueError as e:
            # Use redact_url so we never log session tokens from sub-resources.
            logger.warning(
                "blocked SSRF at request time (%s): %s",
                route.request.resource_type,
                redact_url(url),
            )
            logger.debug("ssrf reject reason: %s", e)
            # Race: request may already be done. Best-effort.
            with contextlib.suppress(Exception):
                await route.abort()
            return
        # Same — race, best-effort.
        with contextlib.suppress(Exception):
            await route.continue_()

    # "**/*" matches every URL the page requests.
    await context.route("**/*", _on_request)


# Common selectors for cookie/GDPR consent-banner accept buttons. Tried in
# order; first match wins. Sourced from inspection of the 10-site real-world
# stress run (docs/real-world-coverage.md). The list is conservative — false
# positives risk auto-accepting on sites that didn't intend it.
COMMON_BANNER_DISMISS: list[str] = [
    "#onetrust-accept-btn-handler",
    "#truste-consent-button",
    "button[id*='accept-cookies' i]",
    "button[id*='gdpr-accept' i]",
    "button[data-testid='cookie-policy-banner-accept']",
    "[aria-label='Accept all' i]",
    "[aria-label='Accept all cookies' i]",
    ".CookieBanner button.accept",
    "#cookie-banner button.accept",
    "button.cc-accept",
    "button.cookie-accept",
    '.govuk-cookie-banner button:has-text("Accept additional cookies")',
]

COMMON_BANNER_FOLLOWUP_DISMISS: list[str] = [
    '.govuk-cookie-banner button:has-text("Hide cookie message")',
]


@dataclass(frozen=True)
class BannerDismissalResult:
    """Machine-readable outcome for optional consent-banner handling."""

    accepted_selector: str | None = None
    followup_selector: str | None = None

    @property
    def dismissed(self) -> bool:
        return self.accepted_selector is not None

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "dismissed": self.dismissed,
            "accepted_selector": self.accepted_selector,
            "followup_selector": self.followup_selector,
        }


async def _dismiss_common_banners(page: Page) -> BannerDismissalResult:
    """Dismiss a known consent banner and any known confirmation state.

    The closed selector lists avoid broad text matching, while the structured
    result makes it possible to distinguish "requested but nothing matched"
    from a capture that never attempted dismissal.
    """
    for selector in COMMON_BANNER_DISMISS:
        try:
            await page.click(selector, timeout=500)
            logger.info("dismissed banner via selector: %s", selector)
        # Each selector is a closed, best-effort probe; one mismatch must not
        # prevent trying the next known consent-banner pattern.
        except Exception:  # nosec B112
            continue
        await page.wait_for_timeout(150)
        for followup_selector in COMMON_BANNER_FOLLOWUP_DISMISS:
            try:
                await page.click(followup_selector, timeout=500)
                logger.info(
                    "dismissed banner confirmation via selector: %s",
                    followup_selector,
                )
                return BannerDismissalResult(selector, followup_selector)
            # Confirmation controls vary independently of the first banner.
            except Exception:  # nosec B112
                continue
        return BannerDismissalResult(accepted_selector=selector)
    return BannerDismissalResult()


async def _goto_with_retries(page, url: str, timeout_ms: int) -> None:
    """page.goto with exponential-backoff retries on timeout/net errors."""
    # Late import so module import succeeds when playwright isn't installed
    # (the CLI's `doctor` subcommand should still work).
    from playwright.async_api import TimeoutError as PWTimeoutError

    backoffs = (1.0, 2.0, 4.0)
    last_exc: BaseException | None = None
    for attempt in range(1, 4):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return
        except PWTimeoutError as e:
            last_exc = e
            reason = f"timeout after {timeout_ms}ms"
            logger.warning("goto attempt %d/3 failed: %s", attempt, reason)
        except Exception as e:
            msg = str(e)
            if "net::" not in msg:
                # Not a retryable network error — bubble up immediately.
                raise
            last_exc = e
            code_match = re.search(r"net::[A-Z0-9_]+", msg)
            code = code_match.group(0) if code_match else "browser-network-error"
            logger.warning(
                "goto attempt %d/3 failed for %s: %s",
                attempt,
                redact_url(url),
                code,
            )
        if attempt < 3:
            await asyncio.sleep(backoffs[attempt - 1])
    # All three attempts failed.
    logger.error(
        "goto failed after 3 attempts for %s: %s",
        redact_url(url),
        type(last_exc).__name__ if last_exc is not None else "unknown-error",
    )
    if last_exc is None:  # Defensive invariant; do not rely on assert under -O.
        raise RuntimeError("goto retries exhausted without a captured exception")
    raise last_exc


async def _install_websocket_block(context) -> dict[str, int]:
    """Close WebSockets and expose whether the captured surface attempted one."""
    status = {"attempted": 0}

    async def _close_web_socket(route) -> None:
        status["attempted"] += 1
        await route.close(code=1008, reason="WebSockets disabled during Keen capture")

    await context.route_web_socket("**/*", _close_web_socket)
    return status


def _interactive_terminal_available() -> bool:
    return bool(sys.stdin is not None and sys.stdin.isatty())


def _wait_for_interactive_auth_confirmation() -> None:
    """Wait for an explicit terminal confirmation without reading credentials."""
    print(
        "Keen opened Chromium for authentication.\n"
        "Sign in normally, then return to this terminal and press Enter. "
        "Keen will reuse the resulting ephemeral browser state without writing it "
        "to the review artifacts.",
        file=sys.stderr,
        flush=True,
    )
    if sys.stdin.readline() == "":
        raise ValueError("interactive authentication ended before confirmation")


async def _snapshot_session_storage(page: Page) -> dict[str, dict[str, str]]:
    """Capture bounded sessionStorage for the current origin in memory."""
    payload = await page.evaluate(
        """
        () => ({
          origin: window.location.origin,
          entries: Object.entries(window.sessionStorage),
        })
        """
    )
    if not isinstance(payload, dict):
        return {}
    origin = payload.get("origin")
    entries = payload.get("entries")
    if not isinstance(origin, str) or not origin or not isinstance(entries, list):
        return {}
    if len(entries) > MAX_SESSION_STORAGE_ENTRIES:
        raise ValueError(
            "authenticated sessionStorage exceeds the safe entry limit; "
            "use declarative cookie/localStorage authentication instead"
        )
    seed: dict[str, str] = {}
    total_bytes = 0
    for entry in entries:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not isinstance(entry[1], str)
        ):
            continue
        key, value = entry
        total_bytes += len(key.encode("utf-8")) + len(value.encode("utf-8"))
        if total_bytes > MAX_SESSION_STORAGE_BYTES:
            raise ValueError(
                "authenticated sessionStorage exceeds the safe in-memory size limit; "
                "use declarative cookie/localStorage authentication instead"
            )
        seed[key] = value
    return {origin: seed} if seed else {}


def _same_product_origin(target: str, observed: str) -> bool:
    target_id = _canonical_url_identity(target)
    observed_id = _canonical_url_identity(observed)
    if target_id is None or observed_id is None:
        return False
    target_scheme, target_host, target_port, *_ = target_id
    observed_scheme, observed_host, observed_port, *_ = observed_id
    return target_host == observed_host and (
        (target_scheme, target_port) == (observed_scheme, observed_port)
        or (
            target_scheme == "http"
            and target_port == 80
            and observed_scheme == "https"
            and observed_port == 443
        )
    )


async def _snapshot_product_session_storage(
    context,
    preferred_page,
    target: str,
) -> dict[str, dict[str, str]]:
    """Capture sessionStorage only from live product-origin tabs, never an IdP."""
    pages = [preferred_page]
    pages.extend(reversed(context.pages))
    seen: set[int] = set()
    merged: dict[str, dict[str, str]] = {}
    for page in pages[:16]:
        marker = id(page)
        if marker in seen:
            continue
        seen.add(marker)
        page_url = getattr(page, "url", None)
        if page is preferred_page:
            if not isinstance(page_url, str) or not _same_product_origin(target, page_url):
                continue
        elif isinstance(page_url, str) and not _same_product_origin(target, page_url):
            continue
        try:
            snapshot = await _snapshot_session_storage(page)
        except ValueError:
            raise
        except Exception as exc:
            logger.info(
                "sessionStorage snapshot skipped for one browser tab: %s",
                type(exc).__name__,
            )
            continue
        for origin, entries in snapshot.items():
            if not _same_product_origin(target, origin):
                continue
            merged.setdefault(origin, {}).update(entries)
    encoded = json.dumps(merged, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_SESSION_STORAGE_BYTES:
        raise ValueError("authenticated sessionStorage exceeds the safe in-memory size limit")
    return merged


def _bounded_auth_storage_state(storage_state: Any) -> dict[str, Any]:
    if not isinstance(storage_state, dict):
        raise ValueError("browser returned an invalid authentication storage state")
    try:
        encoded = json.dumps(
            storage_state,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("browser returned an unserializable authentication storage state") from exc
    if len(encoded) > MAX_AUTH_STORAGE_STATE_BYTES:
        raise ValueError(
            "authenticated browser state exceeds the safe in-memory size limit; "
            "clear unnecessary site data or use narrower declarative authentication"
        )
    return storage_state


async def _install_session_storage_seed(
    context,
    session_storage: dict[str, dict[str, str]],
) -> None:
    """Restore origin-scoped sessionStorage before application scripts run."""
    if not session_storage:
        return
    # JSON serialization makes keys and values inert JavaScript data. The
    # generated init script exists only in the browser process and is never
    # written to Keen artifacts.
    seed_json = json.dumps(session_storage, ensure_ascii=True, separators=(",", ":"))
    await context.add_init_script(
        script=(
            "(() => {"
            f"const seed={seed_json};"
            "const entries=seed[window.location.origin];"
            "if(!entries)return;"
            "for(const [key,value] of Object.entries(entries)){"
            "window.sessionStorage.setItem(key,value);"
            "}"
            "})();"
        )
    )


async def _prepare_auth_storage_state(
    browser,
    cfg: CaptureConfig,
    presets: dict[str, dict[str, int]],
) -> AuthSession | None:
    """Authenticate once in memory, then clone the resulting state per capture."""
    if cfg.auth_steps_path is None and cfg.auth_script is None and not cfg.interactive_auth:
        return None
    preset = presets[cfg.viewports[0]]
    context = await browser.new_context(
        viewport={"width": preset["width"], "height": preset["height"]},
        device_scale_factor=preset["deviceScaleFactor"],
        accept_downloads=False,
        service_workers="block",
        bypass_csp=False,
    )
    try:
        await context.clear_cookies()
        await context.clear_permissions()
        await _install_ssrf_route_guard(
            context,
            allow_internal=cfg.allow_internal,
            allow_file=cfg.allow_file,
            file_roots=cfg.file_request_roots,
            file_documents=cfg.file_document_paths,
            internal_origins=cfg.internal_request_origins,
        )
        websocket_status = await _install_websocket_block(context)
        page = await context.new_page()
        if cfg.interactive_auth:
            await _goto_with_retries(page, cfg.target, cfg.goto_timeout_ms)
            await asyncio.to_thread(_wait_for_interactive_auth_confirmation)
        elif cfg.auth_steps_path is not None:
            await apply_auth_steps(
                page,
                cfg.auth_steps_path,
                allow_internal=cfg.allow_internal,
                allow_file=cfg.allow_file,
            )
        else:
            await _maybe_login(page, cfg.auth_script)
        if websocket_status["attempted"]:
            logger.warning(
                "authentication surface attempted %d WebSocket connection(s); Keen blocked them",
                websocket_status["attempted"],
            )
        logger.info("authentication completed once; reusing in-memory storage state")
        storage_state = _bounded_auth_storage_state(await context.storage_state(indexed_db=True))
        return AuthSession(
            # Some authentication SDKs persist their tokens in IndexedDB.
            # Include it in the ephemeral state clone so interactive sign-in
            # works without writing a reusable credential file to disk.
            storage_state=storage_state,
            session_storage=await _snapshot_product_session_storage(context, page, cfg.target),
        )
    finally:
        await context.close()


async def _capture_one(
    browser,
    cfg: CaptureConfig,
    presets: dict[str, dict[str, int]],
    states: dict[str, dict[str, Any]],
    viewport: str,
    state: str,
    auth_session: AuthSession | None = None,
) -> tuple[Path, Path]:
    # SSRF guard — re-validate per task so the gate is local to capture and
    # not just at CLI parse time.
    validate_target(
        cfg.target,
        allow_internal=cfg.allow_internal,
        allow_file=cfg.allow_file,
    )

    # The matrix is validated before Chromium starts. Index directly here so
    # a programming error cannot silently fall back to an unrequested state.
    preset = presets[viewport]
    state_spec = states[state]

    context_options: dict[str, Any] = {
        "viewport": {"width": preset["width"], "height": preset["height"]},
        "device_scale_factor": preset["deviceScaleFactor"],
        "accept_downloads": False,
        "service_workers": "block",
        "bypass_csp": False,
    }
    if auth_session is not None:
        context_options["storage_state"] = auth_session.storage_state
    context = await browser.new_context(
        **context_options,
    )
    try:
        # Defeat cross-target leakage between siblings sharing a browser.
        if auth_session is None:
            await context.clear_cookies()
        await context.clear_permissions()

        # Request-time SSRF guard. Must be installed BEFORE any page.goto
        # (including the auth-step goto) so DNS rebinding / redirect chains
        # / metadata-endpoint sub-resources can't bypass the pre-flight
        # check by being a different host than the validator saw.
        await _install_ssrf_route_guard(
            context,
            allow_internal=cfg.allow_internal,
            allow_file=cfg.allow_file,
            file_roots=cfg.file_request_roots,
            file_documents=cfg.file_document_paths,
            internal_origins=cfg.internal_request_origins,
        )
        websocket_status = await _install_websocket_block(context)
        if auth_session is not None:
            await _install_session_storage_seed(context, auth_session.session_storage)

        page = await context.new_page()
        # Auth: prefer declarative steps; fall back to script only if no
        # steps file is set (the CLI has already gated --auth-script behind
        # --unsafe-auth-script + cwd containment).
        if auth_session is None:
            if cfg.auth_steps_path is not None:
                await apply_auth_steps(
                    page,
                    cfg.auth_steps_path,
                    allow_internal=cfg.allow_internal,
                    allow_file=cfg.allow_file,
                )
            else:
                await _maybe_login(page, cfg.auth_script)

        await _goto_with_retries(page, cfg.target, cfg.goto_timeout_ms)

        try:
            await page.evaluate("document.fonts && document.fonts.ready")
        except Exception as e:
            logger.warning("document.fonts.ready missed: %s", type(e).__name__)
        await page.wait_for_timeout(250)

        expectation_failure = _url_expectation_failure(cfg, str(page.url))
        if expectation_failure is None and cfg.wait_selector:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            try:
                await page.wait_for_selector(cfg.wait_selector, timeout=15_000)
            except PlaywrightTimeoutError:
                expectation_failure = {
                    "complete": False,
                    "status": "blocked",
                    "reason_code": "missing-selector",
                    "message": "The expected selector was not present on the captured surface.",
                    "target": redact_url(cfg.target),
                    "expected_url": redact_url(cfg.expect_url or cfg.target),
                    "observed_url": redact_url(str(page.url)),
                    "expected_selector": cfg.wait_selector,
                }

        # Hydration settle: let JS-rendered content paint over skeletons.
        # Real-world testing showed sites like nytimes.com and notion.so capture
        # gray placeholders without this. 0 = no extra wait. Preserve the
        # historical contract by settling after the expected selector resolves.
        if expectation_failure is None and cfg.settle_ms > 0:
            await page.wait_for_timeout(min(cfg.settle_ms, 30_000))
            expectation_failure = _url_expectation_failure(cfg, str(page.url))

        # Cookie/GDPR banner dismissal: try a closed list of common accept
        # selectors with a 500ms per-selector timeout. Click the first match.
        banner_dismissal = BannerDismissalResult()
        if expectation_failure is None and cfg.dismiss_banners:
            banner_dismissal = await _dismiss_common_banners(page)

        if expectation_failure is None:
            for step in state_spec.get("setup_steps", []):
                await _apply_setup_step(context, page, step)
            await page.wait_for_timeout(150)
            expectation_failure = _url_expectation_failure(cfg, str(page.url))

        slug = _slug(cfg.target)
        screen_path = cfg.outdir / "screens" / f"{slug}-{viewport}-{state}.png"
        dom_path = cfg.outdir / "dom" / f"{slug}-{viewport}-{state}.json"
        safeio_mod.ensure_output_dir(cfg.outdir, screen_path.parent)
        safeio_mod.ensure_output_dir(cfg.outdir, dom_path.parent)

        # Playwright's page.evaluate has no `timeout` kwarg; wrap with asyncio.wait_for instead.
        dom = await asyncio.wait_for(page.evaluate(INSTRUMENT_JS), timeout=60.0)
        screenshot_options, screenshot_coverage = _screenshot_options(cfg, preset, dom)
        screenshot_bytes = await page.screenshot(**screenshot_options)
        safeio_mod.atomic_write_bytes(cfg.outdir, screen_path, screenshot_bytes)
        accessibility_name_coverage: dict[str, Any]
        try:
            accessibility_name_coverage = await _enrich_accessibility_names(page, dom)
        except Exception as e:
            # Browser accessibility enrichment improves semantic accuracy but
            # must not turn a successful visual capture into a failed run.
            logger.warning(
                "browser accessibility-name enrichment missed: %s",
                type(e).__name__,
            )
            accessibility_name_coverage = {
                "eligible": 0,
                "attempted": 0,
                "enriched": 0,
                "complete": False,
                "reason": "accessibility-name-error",
            }
        # A route can redirect after the initial readiness checks. Re-check at
        # the evidence boundary so a screenshot of a late auth/error surface
        # cannot be declared reviewable.
        if expectation_failure is None:
            expectation_failure = _url_expectation_failure(cfg, str(page.url))
        if expectation_failure is None and cfg.wait_selector:
            try:
                selector_visible = await page.locator(cfg.wait_selector).first.is_visible()
            except Exception:
                selector_visible = False
            if not selector_visible:
                expectation_failure = {
                    "complete": False,
                    "status": "blocked",
                    "reason_code": "missing-selector",
                    "message": "The expected selector was not present on the captured surface.",
                    "target": redact_url(cfg.target),
                    "expected_url": redact_url(cfg.expect_url or cfg.target),
                    "observed_url": redact_url(str(page.url)),
                    "expected_selector": cfg.wait_selector,
                }
        # Redact tokens/fragments from the URL we persist.
        observed_url = dom.get("url")
        if isinstance(observed_url, str):
            dom["url"] = redact_url(observed_url)
        sanitize_dom_payload(dom)
        dom_coverage = dom.get("coverage") or {}
        visible_elements = dom.get("elements")
        surface = dom.get("surface") if isinstance(dom.get("surface"), dict) else {}
        rendered_surface = (
            (isinstance(visible_elements, list) and bool(visible_elements))
            or (
                isinstance(surface.get("body_text_chars"), (int, float))
                and not isinstance(surface.get("body_text_chars"), bool)
                and float(surface["body_text_chars"]) > 0
            )
            or surface.get("painted_background") is True
            or surface.get("pseudo_content") is True
        )
        empty_surface = not rendered_surface
        combined_coverage = {
            **dom_coverage,
            "complete": bool(dom_coverage.get("complete", True))
            and bool(screenshot_coverage.get("complete", True))
            and bool(accessibility_name_coverage.get("complete"))
            and websocket_status["attempted"] == 0
            and not empty_surface
            and expectation_failure is None,
            "dom": dom_coverage,
            "screenshot": screenshot_coverage,
            "accessibility_names": accessibility_name_coverage,
            "expectation": expectation_failure
            or {
                "complete": True,
                "status": "matched",
                "reason_code": None,
                "target": redact_url(cfg.target),
                "expected_url": redact_url(cfg.expect_url or cfg.target),
                "observed_url": redact_url(str(page.url)),
                "expected_selector": cfg.wait_selector,
            },
        }
        combined_coverage["reason"] = (
            (expectation_failure or {}).get("reason_code")
            or ("empty-surface" if empty_surface else None)
            or ("websockets-blocked" if websocket_status["attempted"] else None)
            or accessibility_name_coverage.get("reason")
            or dom_coverage.get("reason")
            or screenshot_coverage.get("reason")
        )
        if not combined_coverage["complete"]:
            logger.warning(
                "capture coverage is partial for %s/%s: %s",
                viewport,
                state,
                combined_coverage.get("reason"),
            )
        dom["coverage"] = combined_coverage
        dom["meta"] = {
            "viewport": viewport,
            "viewport_size": preset,
            "state": state,
            "screen_path": str(screen_path.relative_to(cfg.outdir)),
            "manual_review_needed": bool(state_spec.get("manual_review_needed", False))
            or websocket_status["attempted"] > 0
            or empty_surface
            or not bool(accessibility_name_coverage.get("complete")),
            "target": redact_url(cfg.target),
            "truncated": bool(dom.get("truncated", False)),
            "coverage": combined_coverage,
            "websockets_blocked": {
                "policy": True,
                "attempted": websocket_status["attempted"],
            },
            "empty_surface": empty_surface,
            "banner_dismissal": {
                "requested": cfg.dismiss_banners,
                **banner_dismissal.to_dict(),
            },
        }
        safeio_mod.atomic_write_text(
            cfg.outdir,
            dom_path,
            json.dumps(dom, indent=2),
            encoding="utf-8",
        )

        if expectation_failure is not None:
            expected_selector = expectation_failure.get("expected_selector")
            selector_detail = (
                f" Expected selector {expected_selector!r}."
                if isinstance(expected_selector, str)
                else ""
            )
            raise CaptureBlocked(
                reason_code=str(expectation_failure["reason_code"]),
                message=(
                    f"{expectation_failure['message']} "
                    f"Expected {expectation_failure['expected_url']}; "
                    f"observed {expectation_failure['observed_url']}."
                    f"{selector_detail}"
                ),
                screen_path=screen_path,
                dom_path=dom_path,
                expected_url=str(expectation_failure["expected_url"]),
                observed_url=str(expectation_failure["observed_url"]),
                expected_selector=(
                    expected_selector if isinstance(expected_selector, str) else None
                ),
            )

        return screen_path, dom_path
    finally:
        await context.close()


def _concurrency() -> int:
    raw = os.environ.get("KEEN_CONCURRENCY", "4")
    try:
        n = int(raw)
    except ValueError:
        logger.warning("KEEN_CONCURRENCY=%r is not an int; using 4", raw)
        return 4
    if n > MAX_CAPTURE_CONCURRENCY:
        logger.warning(
            "KEEN_CONCURRENCY=%d exceeds the safe cap; using %d",
            n,
            MAX_CAPTURE_CONCURRENCY,
        )
    return min(MAX_CAPTURE_CONCURRENCY, max(1, n))


def _validate_requested_matrix(
    cfg: CaptureConfig,
    presets: dict[str, dict[str, int]],
    states: dict[str, dict[str, Any]],
) -> None:
    """Fail before browser launch for unsafe, empty, duplicate, or unknown IDs."""
    for kind, requested, available in (
        ("viewport", cfg.viewports, presets),
        ("state", cfg.states, states),
    ):
        if not requested:
            raise ValueError(f"at least one {kind} is required")
        if len(requested) != len(set(requested)):
            raise ValueError(f"duplicate {kind}s are not allowed: {requested}")
        for value in requested:
            _validate_artifact_id(value, kind=kind)
        unknown = sorted(set(requested) - set(available))
        if unknown:
            raise ValueError(
                f"unknown {kind}{'s' if len(unknown) != 1 else ''}: "
                f"{', '.join(unknown)}. Available: {', '.join(sorted(available))}"
            )


def preflight(
    cfg: CaptureConfig,
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, Any]]]:
    """Validate a capture completely without creating or deleting artifacts."""
    validate_target(
        cfg.target,
        allow_internal=cfg.allow_internal,
        allow_file=cfg.allow_file,
    )
    _validate_capture_expectations(cfg)
    if cfg.allow_origins and not cfg.allow_internal:
        raise ValueError("--allow-origin requires --allow-internal")
    for origin_url in cfg.allow_origins:
        network_origin(origin_url, require_origin_only=True)
        validate_target(origin_url, allow_internal=True)
    if cfg.expect_url is not None and "*" not in cfg.expect_url:
        validate_target(
            cfg.expect_url,
            allow_internal=cfg.allow_internal,
            allow_file=cfg.allow_file,
        )
    auth_modes = sum(
        (
            cfg.auth_steps_path is not None,
            cfg.auth_script is not None,
            cfg.interactive_auth,
        )
    )
    if auth_modes > 1:
        raise ValueError(
            "choose exactly one authentication mode: auth steps, auth script, or interactive auth"
        )
    if cfg.interactive_auth and not _interactive_terminal_available():
        raise ValueError(
            "--interactive-auth requires an interactive terminal; "
            "use --auth-steps for unattended or agent-driven capture"
        )
    auth_steps: list[dict[str, Any]] | None = None
    if cfg.auth_steps_path is not None:
        auth_steps = load_and_validate_auth_steps(
            cfg.auth_steps_path,
            allow_internal=cfg.allow_internal,
            allow_file=cfg.allow_file,
        )
        _validate_auth_environment(auth_steps)
    if cfg.auth_script is not None and not cfg.auth_script.is_file():
        raise ValueError(f"auth script not found: {cfg.auth_script}")

    presets = load_viewport_presets(cfg.viewport_config)
    states = load_states(cfg.states_config)
    _validate_requested_matrix(cfg, presets, states)
    if cfg.allow_file:
        cfg.file_request_roots, cfg.file_document_paths = _derive_file_request_policy(
            cfg.target,
            cfg.expect_url,
            auth_steps,
        )
    else:
        cfg.file_request_roots = ()
        cfg.file_document_paths = ()
    cfg.internal_request_origins = (
        _derive_internal_request_origins(
            cfg.target,
            cfg.expect_url,
            auth_steps,
            cfg.allow_origins,
        )
        if cfg.allow_internal
        else ()
    )
    return presets, states


async def _run_async(cfg: CaptureConfig) -> None:
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "Playwright is required. Install with:\n"
            "  pip install playwright && playwright install chromium"
        ) from e

    presets, states = preflight(cfg)

    safeio_mod.ensure_output_dir(cfg.outdir, cfg.outdir)
    sem = asyncio.Semaphore(_concurrency())

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=not cfg.interactive_auth)
        except PlaywrightError as exc:
            raise CaptureFailure(_browser_launch_failure_message(exc)) from None
        try:
            try:
                auth_session = await _prepare_auth_storage_state(browser, cfg, presets)
            except Exception as exc:
                # Playwright includes the full navigation URL in many errors.
                # Authentication URLs routinely carry OAuth codes, signed
                # callbacks, or session tokens, so never forward the raw
                # exception through the CLI's traceback handler.
                logger.error("browser authentication failed: %s", type(exc).__name__)
                raise CaptureFailure(
                    "browser authentication failed; no capture artifacts were written"
                ) from None
            pairs: list[tuple[str, str]] = [(vp, st) for vp in cfg.viewports for st in cfg.states]
            in_progress_path = cfg.outdir / artifacts_mod.CAPTURE_IN_PROGRESS_FILENAME
            safeio_mod.atomic_write_text(
                cfg.outdir,
                in_progress_path,
                '{"schema_version":1,"status":"capture-in-progress"}\n',
                encoding="utf-8",
            )

            async def _one(viewport: str, state: str) -> tuple[Path, Path]:
                async with sem:
                    logger.info("capture %s/%s", viewport, state)
                    return await _capture_one(
                        browser,
                        cfg,
                        presets,
                        states,
                        viewport,
                        state,
                        auth_session,
                    )

            tasks = [
                asyncio.create_task(_one(vp, st), name=f"capture-{vp}-{st}") for (vp, st) in pairs
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            succeeded: list[dict[str, str]] = []
            failures: list[dict[str, Any]] = []
            for (vp, st), res in zip(pairs, results, strict=True):
                if isinstance(res, BaseException):
                    failure: dict[str, Any] = {
                        "viewport": vp,
                        "state": st,
                        "reason": f"{type(res).__name__}: browser capture failed",
                        "reason_code": "capture-error",
                    }
                    if isinstance(res, CaptureBlocked):
                        failure.update(
                            {
                                "reason_code": res.reason_code,
                                "screen": str(res.screen_path.relative_to(cfg.outdir)),
                                "dom": str(res.dom_path.relative_to(cfg.outdir)),
                                "diagnostic_only": True,
                                "expected_url": res.expected_url,
                                "observed_url": res.observed_url,
                                "expected_selector": res.expected_selector,
                            }
                        )
                    failures.append(failure)
                else:
                    screen_path, dom_path = res
                    succeeded.append(
                        {
                            "viewport": vp,
                            "state": st,
                            "screen": str(screen_path.relative_to(cfg.outdir)),
                            "dom": str(dom_path.relative_to(cfg.outdir)),
                        }
                    )

            total = len(pairs)
            manifest = {
                "target": redact_url(cfg.target),
                "requested": [{"viewport": viewport, "state": state} for viewport, state in pairs],
                "succeeded": succeeded,
                "failures": failures,
            }
            manifest = artifacts_mod.validate_capture_manifest(cfg.outdir, manifest)
            safeio_mod.atomic_write_text(
                cfg.outdir,
                cfg.outdir / "capture-manifest.json",
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            # The manifest is the commit record for the capture. Remove the
            # marker only after its atomic installation; interrupted runs keep
            # the marker so current partial artifacts cannot be mistaken for
            # compatible manifest-less legacy evidence.
            safeio_mod.remove_output_file(cfg.outdir, in_progress_path)
            logger.info(
                "captured %d/%d (%d failures)",
                len(succeeded),
                total,
                len(failures),
            )
            for failure in failures:
                logger.error(
                    "  failed %s/%s: %s",
                    failure["viewport"],
                    failure["state"],
                    failure["reason"],
                )
            if failures or len(succeeded) != total:
                raise CaptureFailure(
                    f"capture incomplete: {len(succeeded)}/{total} succeeded; "
                    "see capture-manifest.json for details"
                )
        finally:
            await browser.close()


def run(cfg: CaptureConfig) -> None:
    """Synchronous entry point used by cli.py."""
    asyncio.run(_run_async(cfg))
