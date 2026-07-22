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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from harness._assets import asset_path
from harness._sanitize import sanitize_dom_payload
from harness._urlsafe import (
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


class CaptureFailure(RuntimeError):
    """Raised when one or more requested captures fail."""


def _browser_launch_failure_message(exc: Exception) -> str:
    if "Executable doesn't exist" in str(exc):
        return (
            "Chromium is not installed for this Playwright environment; "
            "run `python -m playwright install chromium` with the same Python interpreter"
        )
    return f"could not launch Chromium: {exc}"


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

  function isVisible(el, rect, cs) {
    if (rect.width === 0 || rect.height === 0) return false;
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity) === 0) return false;
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

  function pickStyles(el) {
    const cs = window.getComputedStyle(el);
    const out = {};
    for (const k of STYLE_PROPS) out[k] = cs[k];
    return [out, cs];
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

  let focusedIndex = -1;

  for (const entry of visible) {
    const { el, rect, styles } = entry;

    let parentIndex = -1;
    let p = el.parentElement;
    while (p) {
      if (indexMap.has(p)) { parentIndex = indexMap.get(p); break; }
      p = p.parentElement;
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
      ariaDisabled: el.getAttribute('aria-disabled') === 'true',
      ariaModal: el.getAttribute('aria-modal') === 'true',
      ariaCurrent: el.getAttribute('aria-current') || null,
      hasAlt: el.tagName === 'IMG' ? el.hasAttribute('alt') : null,
      autocomplete: el.getAttribute('autocomplete') || null,
      inputmode: el.getAttribute('inputmode') || null,
      required: el.required || el.getAttribute('aria-required') === 'true' || false,
      disabled: el.disabled || false,
      tabIndex: el.tabIndex,
      name: accessibleName(el),
      placeholder: el.getAttribute('placeholder') || null,
      text,
      box: {
        x: Math.round(rect.x + window.scrollX),
        y: Math.round(rect.y + window.scrollY),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      },
      styles,
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


def validate_auth_steps(data: Any) -> list[dict[str, Any]]:
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
            # wait_for_url to capture credentials. allow_internal=False
            # closes the door; the caller can override via the
            # --allow-internal CLI flag, but only by patching the dispatch
            # site, not via the JSON.
            try:
                validate_target(url, allow_internal=False, allow_file=False)
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
            if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 0:
                raise ValueError(f"{where}: 'timeout_ms' must be a non-negative int")
            # wait_for_url has its own hard ceiling so a hostile JSON can't
            # set timeout_ms = 24 hours and stall the run forever. Other
            # actions inherit Playwright's own default semantics.
            if action == "wait_for_url" and timeout > _WAIT_TIMEOUT_MAX_MS:
                raise ValueError(
                    f"{where}: 'wait_for_url.timeout_ms' exceeds cap of "
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


def load_and_validate_auth_steps(steps_path: Path) -> list[dict[str, Any]]:
    """Load + size-cap + parse + schema-validate the auth-steps JSON.

    Split out from ``apply_auth_steps`` so callers can fail fast BEFORE
    launching Playwright. Failing here on schema/SSRF/file-size catches
    bad input in milliseconds, vs. seconds spent spinning up a browser
    just to reject the same JSON.

    Hardening:
      * File size cap (256 KiB) catches gigabyte-JSON DoS.
      * ``Path.stat()`` + ``read_text`` keeps the whole file in memory
        only after the cap check passes.
      * ``validate_auth_steps`` enforces the schema + SSRF guard on
        ``goto`` URLs.

    NOTE on TOCTOU: between ``stat()`` and ``read_text()``, an attacker
    who controls the file path could swap the file. The size check would
    no longer match what we parse. Mitigation here is single-process
    reading and the JSON-decode-then-validate flow: the validator still
    runs against whatever bytes we actually read, so a swap can only
    weaken the size cap, not the schema gate. Stronger fix would be
    opening the file then fstat'ing the fd, but Path.read_text doesn't
    expose that. Documented for now.
    """
    if not steps_path.exists():
        raise FileNotFoundError(f"auth-steps file not found: {steps_path}")
    try:
        size = steps_path.stat().st_size
    except OSError as e:
        raise ValueError(f"auth-steps file stat failed for {steps_path}: {e}") from e
    if size > _AUTH_STEPS_MAX_BYTES:
        raise ValueError(
            f"auth-steps file too large: {size} bytes exceeds cap of "
            f"{_AUTH_STEPS_MAX_BYTES} bytes (256 KiB). Trim the JSON or "
            "raise the cap if your login flow genuinely needs that much."
        )
    try:
        data = json.loads(steps_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"auth-steps JSON parse error in {steps_path}: {e}") from e
    return validate_auth_steps(data)


async def apply_auth_steps(page: Page, steps_path: Path) -> None:
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
    steps = load_and_validate_auth_steps(steps_path)
    n = len(steps)
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
            validate_target(step["url"], allow_internal=False, allow_file=False)
            logger.info("auth step %d/%d: goto %s", i, n, redact_url(step["url"]))
            await page.goto(
                step["url"],
                timeout=int(step.get("timeout_ms", 30000)),
                wait_until="domcontentloaded",
            )
        elif action == "wait_for_selector":
            sel = step["selector"]
            logger.info("auth step %d/%d: wait_for_selector %s", i, n, sel)
            kwargs: dict[str, Any] = {
                "timeout": int(step.get("timeout_ms", _WAIT_TIMEOUT_DEFAULT_MS))
            }
            if step.get("state"):
                kwargs["state"] = step["state"]
            await page.wait_for_selector(sel, **kwargs)
        elif action == "wait_for_url":
            pattern = step["pattern"]
            # Cap the wait. Validator already enforces the ceiling on
            # explicit timeout_ms, but clamp here too for belt + braces.
            requested = int(step.get("timeout_ms", _WAIT_TIMEOUT_DEFAULT_MS))
            timeout_ms = min(requested, _WAIT_TIMEOUT_MAX_MS)
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
            await page.fill(sel, value, timeout=int(step.get("timeout_ms", 15000)))
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
            validate_target(current_url, allow_internal=False, allow_file=False)
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
            kwargs = {"timeout": int(step.get("timeout_ms", 15000))}
            if step.get("button"):
                kwargs["button"] = step["button"]
            await page.click(sel, **kwargs)
        elif action == "press":
            sel = step["selector"]
            key = step["key"]
            logger.info("auth step %d/%d: press %s on %s", i, n, key, sel)
            await page.press(sel, key, timeout=int(step.get("timeout_ms", 15000)))
        elif action == "check":
            sel = step["selector"]
            logger.info("auth step %d/%d: check %s", i, n, sel)
            await page.check(sel, timeout=int(step.get("timeout_ms", 15000)))
        elif action == "uncheck":
            sel = step["selector"]
            logger.info("auth step %d/%d: uncheck %s", i, n, sel)
            await page.uncheck(sel, timeout=int(step.get("timeout_ms", 15000)))
        elif action == "select_option":
            sel = step["selector"]
            logger.info("auth step %d/%d: select_option %s", i, n, sel)
            if "values" in step:
                await page.select_option(
                    sel,
                    step["values"],
                    timeout=int(step.get("timeout_ms", 15000)),
                )
            else:
                await page.select_option(
                    sel,
                    step["value"],
                    timeout=int(step.get("timeout_ms", 15000)),
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
            logger.warning("locator lookup for selector '%s' failed: %s", selector, e)
        except Exception as e:
            logger.warning("locator lookup for selector '%s' missed: %s", selector, e)
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
) -> None:
    """Register a per-context route handler that re-validates every request.

    Pre-flight `validate_target` only checks the initial URL once. That's
    not enough on its own to stop:

        1. DNS rebinding — a malicious DNS server returns a public IP for
           the validator's `getaddrinfo` and a private IP when the browser
           resolves the same name moments later for the actual TCP connect.
        2. Redirect chains — `http://attacker.com/r` -> 302 ->
           `http://169.254.169.254/`. Playwright follows the redirect and
           fetches the metadata endpoint without us ever re-checking.
        3. Sub-resources — a benign-looking page <iframe>s or <img>s a
           private URL.

    The route handler runs on every request the page makes (navigations,
    redirects, sub-resources), so re-validating `request.url` here closes
    all three holes. Aborted requests show up to the page as net::FAILED,
    which `_goto_with_retries` already treats as retryable / reportable.
    """

    async def _on_request(route) -> None:
        url = route.request.url
        try:
            revalidate_target_at_request_time(
                url,
                allow_internal=allow_internal,
                allow_file=allow_file,
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
]


async def _dismiss_common_banners(page: Page) -> str | None:
    """Click the first matching consent-banner button. Returns the selector
    used, or None if no banner was found."""
    for selector in COMMON_BANNER_DISMISS:
        try:
            await page.click(selector, timeout=500)
            logger.info("dismissed banner via selector: %s", selector)
            return selector
        except Exception:
            continue
    return None


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
            logger.warning("goto attempt %d/3 failed: %s", attempt, msg)
        if attempt < 3:
            await asyncio.sleep(backoffs[attempt - 1])
    # All three attempts failed.
    logger.error("goto failed after 3 attempts: %s", last_exc)
    assert last_exc is not None  # for type-checkers
    raise last_exc


async def _install_websocket_block(context) -> None:
    """Close WebSockets because they bypass normal HTTP route interception."""

    async def _close_web_socket(route) -> None:
        await route.close(code=1008, reason="WebSockets disabled during Keen capture")

    await context.route_web_socket("**/*", _close_web_socket)


async def _prepare_auth_storage_state(
    browser,
    cfg: CaptureConfig,
    presets: dict[str, dict[str, int]],
) -> dict[str, Any] | None:
    """Authenticate once in memory, then clone the resulting state per capture."""
    if cfg.auth_steps_path is None and cfg.auth_script is None:
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
        )
        await _install_websocket_block(context)
        page = await context.new_page()
        if cfg.auth_steps_path is not None:
            await apply_auth_steps(page, cfg.auth_steps_path)
        else:
            await _maybe_login(page, cfg.auth_script)
        logger.info("authentication completed once; reusing in-memory storage state")
        return await context.storage_state()
    finally:
        await context.close()


async def _capture_one(
    browser,
    cfg: CaptureConfig,
    presets: dict[str, dict[str, int]],
    states: dict[str, dict[str, Any]],
    viewport: str,
    state: str,
    storage_state: dict[str, Any] | None = None,
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
    if storage_state is not None:
        context_options["storage_state"] = storage_state
    context = await browser.new_context(
        **context_options,
    )
    try:
        # Defeat cross-target leakage between siblings sharing a browser.
        if storage_state is None:
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
        )
        await _install_websocket_block(context)

        page = await context.new_page()
        # Auth: prefer declarative steps; fall back to script only if no
        # steps file is set (the CLI has already gated --auth-script behind
        # --unsafe-auth-script + cwd containment).
        if storage_state is None:
            if cfg.auth_steps_path is not None:
                await apply_auth_steps(page, cfg.auth_steps_path)
            else:
                await _maybe_login(page, cfg.auth_script)

        await _goto_with_retries(page, cfg.target, cfg.goto_timeout_ms)
        if cfg.wait_selector:
            await page.wait_for_selector(cfg.wait_selector, timeout=15_000)

        try:
            await page.evaluate("document.fonts && document.fonts.ready")
        except Exception as e:
            logger.warning("document.fonts.ready missed: %s", e)
        await page.wait_for_timeout(250)

        # Hydration settle: let JS-rendered content paint over skeletons.
        # Real-world testing showed sites like nytimes.com and notion.so capture
        # gray placeholders without this. 0 = no extra wait.
        if cfg.settle_ms > 0:
            await page.wait_for_timeout(min(cfg.settle_ms, 30_000))

        # Cookie/GDPR banner dismissal: try a closed list of common accept
        # selectors with a 500ms per-selector timeout. Click the first match.
        if cfg.dismiss_banners:
            await _dismiss_common_banners(page)

        for step in state_spec.get("setup_steps", []):
            await _apply_setup_step(context, page, step)
        await page.wait_for_timeout(150)

        slug = _slug(cfg.target)
        screen_path = cfg.outdir / "screens" / f"{slug}-{viewport}-{state}.png"
        dom_path = cfg.outdir / "dom" / f"{slug}-{viewport}-{state}.json"
        screen_path.parent.mkdir(parents=True, exist_ok=True)
        dom_path.parent.mkdir(parents=True, exist_ok=True)

        # Playwright's page.evaluate has no `timeout` kwarg; wrap with asyncio.wait_for instead.
        dom = await asyncio.wait_for(page.evaluate(INSTRUMENT_JS), timeout=60.0)
        screenshot_options, screenshot_coverage = _screenshot_options(cfg, preset, dom)
        await page.screenshot(path=str(screen_path), **screenshot_options)
        # Redact tokens/fragments from the URL we persist.
        observed_url = dom.get("url")
        if isinstance(observed_url, str):
            dom["url"] = redact_url(observed_url)
        sanitize_dom_payload(dom)
        dom_coverage = dom.get("coverage") or {}
        combined_coverage = {
            **dom_coverage,
            "complete": bool(dom_coverage.get("complete", True))
            and bool(screenshot_coverage.get("complete", True)),
            "dom": dom_coverage,
            "screenshot": screenshot_coverage,
        }
        if not combined_coverage["complete"]:
            logger.warning(
                "capture coverage is partial for %s/%s: %s",
                viewport,
                state,
                screenshot_coverage.get("reason") or dom_coverage.get("reason"),
            )
        dom["coverage"] = combined_coverage
        dom["meta"] = {
            "viewport": viewport,
            "viewport_size": preset,
            "state": state,
            "screen_path": str(screen_path.relative_to(cfg.outdir)),
            "manual_review_needed": bool(state_spec.get("manual_review_needed", False)),
            "target": redact_url(cfg.target),
            "truncated": bool(dom.get("truncated", False)),
            "coverage": combined_coverage,
            "websockets_blocked": True,
        }
        dom_path.write_text(json.dumps(dom, indent=2), encoding="utf-8")

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


async def _run_async(cfg: CaptureConfig) -> None:
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "Playwright is required. Install with:\n"
            "  pip install playwright && playwright install chromium"
        ) from e

    # Fail fast if the target is unsafe.
    validate_target(
        cfg.target,
        allow_internal=cfg.allow_internal,
        allow_file=cfg.allow_file,
    )

    # Fail fast on auth-steps too: load + parse + schema-validate BEFORE
    # spending the ~1 s it takes to spin up Chromium. The same function
    # gets called again from inside apply_auth_steps; pre-validating here
    # just surfaces bad input earlier and avoids the browser cold start.
    if cfg.auth_steps_path is not None:
        load_and_validate_auth_steps(cfg.auth_steps_path)

    presets = load_viewport_presets(cfg.viewport_config)
    states = load_states(cfg.states_config)
    _validate_requested_matrix(cfg, presets, states)

    cfg.outdir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(_concurrency())

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except PlaywrightError as exc:
            raise CaptureFailure(_browser_launch_failure_message(exc)) from None
        try:
            storage_state = await _prepare_auth_storage_state(browser, cfg, presets)
            pairs: list[tuple[str, str]] = [(vp, st) for vp in cfg.viewports for st in cfg.states]

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
                        storage_state,
                    )

            tasks = [
                asyncio.create_task(_one(vp, st), name=f"capture-{vp}-{st}") for (vp, st) in pairs
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            succeeded: list[dict[str, str]] = []
            failures: list[tuple[str, str, str]] = []
            for (vp, st), res in zip(pairs, results, strict=True):
                if isinstance(res, BaseException):
                    failures.append((vp, st, f"{type(res).__name__}: {res}"))
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
                "failures": [
                    {"viewport": vp, "state": st, "reason": reason} for vp, st, reason in failures
                ],
                "complete": not failures and len(succeeded) == total,
            }
            (cfg.outdir / "capture-manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            logger.info(
                "captured %d/%d (%d failures)",
                len(succeeded),
                total,
                len(failures),
            )
            for vp, st, reason in failures:
                logger.error("  failed %s/%s: %s", vp, st, reason)
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
