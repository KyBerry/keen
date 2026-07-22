"""Render preview HTML for a system JSON, with optional side-by-side comparison.

Wraps the existing systemize preview renderer (which already knows how to draw
color chips, type-scale samples, button samples). Adds a comparison.html that
shows the proposal next to N reference systems in a grid.

The systemize renderer was written against the `propose_system` output shape
(``colors.suggested_roles``, ``radii.scale[].name``, ``type.sizes[].name``,
explicit ``fonts.display`` / ``fonts.body`` roles with ``fonts.primary`` as a
compatibility alias). The newer validate-system / generation flow uses a
slightly different schema (``colors.roles``, ``radii.scale[].role``,
``type.sizes[].role``, no ``fonts``). ``render_preview`` accepts either
shape — it normalizes the validate-system shape into the systemize shape
before delegating, so the existing renderer never sees a missing key.

No new runtime deps. Pure stdlib + harness.
"""

from __future__ import annotations

import html
import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger("keen")


_BUILTIN_THEMES: dict[str, dict[str, str]] = {
    "material-3": {
        "primary": "#6750A4",
        "text": "#1D1B20",
        "subtle": "#49454F",
        "surface": "#FFFBFE",
        "surface_subtle": "#F7F2FA",
        "surface_bold": "#21005D",
        "border": "#79747E",
        "focus": "#6750A4",
        "focus_inverse": "#D0BCFF",
        "font": "Roboto, system-ui, sans-serif",
    },
    "apple-hig": {
        "primary": "#0066CC",
        "text": "#1D1D1F",
        "subtle": "#515154",
        "surface": "#FFFFFF",
        "surface_subtle": "#F5F5F7",
        "surface_bold": "#1D1D1F",
        "border": "#8E8E93",
        "focus": "#0066CC",
        "focus_inverse": "#64D2FF",
        "font": "-apple-system, BlinkMacSystemFont, sans-serif",
    },
    "fluent-2": {
        "primary": "#0F6CBD",
        "text": "#242424",
        "subtle": "#525252",
        "surface": "#FFFFFF",
        "surface_subtle": "#F5F5F5",
        "surface_bold": "#242424",
        "border": "#8A8886",
        "focus": "#0F6CBD",
        "focus_inverse": "#75B6E7",
        "font": "Segoe UI, system-ui, sans-serif",
    },
    "polaris": {
        "primary": "#005BD3",
        "text": "#303030",
        "subtle": "#616161",
        "surface": "#FFFFFF",
        "surface_subtle": "#F7F7F7",
        "surface_bold": "#303030",
        "border": "#8A8A8A",
        "focus": "#005BD3",
        "focus_inverse": "#91C5F7",
        "font": "Inter, system-ui, sans-serif",
    },
    "carbon": {
        "primary": "#0F62FE",
        "text": "#161616",
        "subtle": "#525252",
        "surface": "#FFFFFF",
        "surface_subtle": "#F4F4F4",
        "surface_bold": "#161616",
        "border": "#8D8D8D",
        "focus": "#0F62FE",
        "focus_inverse": "#78A9FF",
        "font": "IBM Plex Sans, system-ui, sans-serif",
    },
    "atlassian": {
        "primary": "#0C66E4",
        "text": "#172B4D",
        "subtle": "#44546F",
        "surface": "#FFFFFF",
        "surface_subtle": "#F7F8F9",
        "surface_bold": "#172B4D",
        "border": "#8590A2",
        "focus": "#0C66E4",
        "focus_inverse": "#85B8FF",
        "font": "system-ui, -apple-system, sans-serif",
    },
}


def builtin_reference_system(name: str) -> dict[str, Any] | None:
    """Build a previewable token specimen for a bundled markdown reference."""
    from harness.tokens import SYSTEM_TOKENS

    canonical = SYSTEM_TOKENS.get(name)
    theme = _BUILTIN_THEMES.get(name)
    if canonical is None or theme is None:
        return None

    font_sizes = sorted({int(v) for v in canonical.get("font_sizes_px", [])})
    if not font_sizes:
        font_sizes = [12, 14, 16, 24, 32, 40]
    body_size = min(font_sizes, key=lambda size: abs(size - 16))
    smaller = [size for size in font_sizes if size < body_size]
    headings = [size for size in font_sizes if size > body_size]
    caption_size = font_sizes[0]
    body_sm_size = min(smaller, key=lambda size: abs(size - 14)) if smaller else body_size
    h3_size = min(headings, key=lambda size: abs(size - 20)) if headings else body_size
    h2_size = min(headings, key=lambda size: abs(size - 28)) if headings else h3_size
    h1_size = headings[-1] if headings else h2_size
    selected = [caption_size, body_sm_size, body_size, h3_size, h2_size, h1_size]
    role_names = ["caption", "body-sm", "body", "h3", "h2", "h1"]
    sizes = [
        {
            "role": role,
            "size_px": size,
            "weight": 600 if role.startswith("h") else 400,
            "line_height": max(size + 4, round(size * 1.3)),
        }
        for role, size in zip(role_names, selected, strict=True)
    ]

    radii = sorted({int(v) for v in canonical.get("border_radii_px", []) if int(v) < 1000})
    positive_radii = [value for value in radii if value > 0]
    radius_scale: list[dict[str, Any]] = [{"role": "none", "px": 0}]
    if positive_radii:
        candidates = [
            positive_radii[0],
            positive_radii[len(positive_radii) // 2],
            positive_radii[-1],
        ]
        for role, value in zip(("sm", "md", "lg"), dict.fromkeys(candidates), strict=False):
            radius_scale.append({"role": role, "px": value})

    grid = int(canonical.get("spacing_grid_px", 4))
    roles = {
        "text.default": {"hex": theme["text"]},
        "text.subtle": {"hex": theme["subtle"]},
        "text.inverse": {"hex": "#FFFFFF"},
        "surface.default": {"hex": theme["surface"]},
        "surface.subtle": {"hex": theme["surface_subtle"]},
        "surface.bold": {"hex": theme["surface_bold"]},
        "border.default": {"hex": theme["border"]},
        "border.focused": {"hex": theme["focus"]},
        "border.focused-inverse": {"hex": theme["focus_inverse"]},
        "primary": {"hex": theme["primary"]},
    }
    return {
        "name": name,
        "version": "reference",
        "archetype": "bundled-reference",
        "fonts": {
            "primary": theme["font"],
            "body": theme["font"],
            "display": theme["font"],
            "mono": 'ui-monospace, "SFMono-Regular", Menlo, monospace',
            "pairing": {
                "mode": "single-family",
                "display_family": theme["font"].split(",", 1)[0],
                "body_family": theme["font"].split(",", 1)[0],
                "rationale": (
                    "This reference system uses one family across display and body roles; "
                    "scale and weight provide the hierarchy."
                ),
                "evidence": [],
            },
        },
        "type": {
            "body_px": body_size,
            "ratio": round((h1_size / body_size) ** (1 / 3), 3),
            "ratio_name": "reference scale",
            "sizes": sizes,
        },
        "spacing": {"grid_px": grid, "scale": [grid * n for n in (1, 2, 3, 4, 6, 8, 12, 16)]},
        "radii": {"scale": radius_scale},
        "colors": {"roles": roles},
    }


# -- Shape adapter --------------------------------------------------------


def _normalize_for_systemize_renderer(system: dict[str, Any]) -> dict[str, Any]:
    """Adapt a validate-system-shaped system to the systemize renderer's shape.

    The systemize ``render_preview_html`` expects:

    - ``proposal["fonts"]["display"]`` and ``proposal["fonts"]["body"]``
    - ``proposal["colors"]["suggested_roles"]``
    - ``proposal["colors"]["accents"]``
    - ``proposal["radii"]["scale"][i]["name"]``
    - ``proposal["type"]["sizes"][i]["name"]``

    The newer validate-system schema uses ``colors.roles``,
    ``radii.scale[].role``, ``type.sizes[].role``, and may omit ``fonts``
    and ``accents`` entirely. This adapter copies the input (so we don't
    mutate the caller's dict) and inserts compatible fallbacks.

    The systemize renderer owns output-context escaping and CSS validation.
    This adapter only normalizes shapes so values are escaped exactly once.
    """
    s = deepcopy(system)

    # Fonts: normalize legacy `primary` into explicit body/display/mono roles.
    fonts = s.get("fonts")
    if not isinstance(fonts, dict):
        fonts = {}
    fallback_font = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
    body = next(
        (
            value
            for value in (fonts.get("body"), fonts.get("default"), fonts.get("primary"))
            if isinstance(value, str) and value
        ),
        fallback_font,
    )
    display = fonts.get("display")
    if not isinstance(display, str) or not display:
        display = body
    fonts["primary"] = body
    fonts["body"] = body
    fonts["display"] = display
    fonts.setdefault("mono", 'ui-monospace, "SFMono-Regular", Menlo, monospace')
    fonts.setdefault(
        "pairing",
        {
            "mode": "single-family",
            "rationale": (
                "Display and body roles share one family until observed evidence supports a pairing."
            ),
        },
    )
    s["fonts"] = fonts

    # Colors: surface `roles` as `suggested_roles` if the renderer-expected
    # key is missing; ensure `accents` exists (renderer iterates it).
    colors = s.get("colors")
    if not isinstance(colors, dict):
        colors = {}
    if "suggested_roles" not in colors:
        roles = colors.get("roles")
        if isinstance(roles, dict):
            colors["suggested_roles"] = roles
        else:
            colors["suggested_roles"] = {}
    if "accents" not in colors or not isinstance(colors["accents"], list):
        colors["accents"] = []
    else:
        # Accents can be strings in the validate-system schema; normalize to
        # the dict shape consumed by the renderer.
        normalized_accents: list[Any] = []
        for a in colors["accents"]:
            if isinstance(a, str):
                normalized_accents.append({"hex": a, "count": 0})
            elif isinstance(a, dict):
                normalized_accents.append(dict(a))
            else:
                continue
        colors["accents"] = normalized_accents
    s["colors"] = colors

    # Radii: each entry must have a `name` key; map from `role` when needed.
    radii = s.get("radii")
    if not isinstance(radii, dict):
        radii = {}
    scale = radii.get("scale") or []
    new_scale: list[dict[str, Any]] = []
    for r in scale:
        if not isinstance(r, dict):
            continue
        entry = dict(r)
        if "name" not in entry:
            entry["name"] = entry.get("role", "?")
        new_scale.append(entry)
    radii["scale"] = new_scale
    s["radii"] = radii

    # Type sizes: each entry must have `name`; map from `role` when needed.
    type_ = s.get("type")
    if not isinstance(type_, dict):
        type_ = {}
    sizes = type_.get("sizes") or []
    new_sizes: list[dict[str, Any]] = []
    for sz in sizes:
        if not isinstance(sz, dict):
            continue
        entry = dict(sz)
        if "name" not in entry:
            entry["name"] = entry.get("role", "?")
        new_sizes.append(entry)
    type_["sizes"] = new_sizes
    s["type"] = type_

    # Spacing: renderer reads sp["grid_px"] and sp["scale"] unguarded. Provide
    # defaults if the system omits the section entirely or only partially fills
    # it. The renderer also formats scale entries directly into widths, so we
    # leave them as-is (they should be ints — strings would already be a schema
    # violation caught upstream).
    sp = s.get("spacing")
    if not isinstance(sp, dict):
        s["spacing"] = {"grid_px": 4, "scale": []}
    else:
        if "grid_px" not in sp:
            sp["grid_px"] = 4
        if not isinstance(sp.get("scale"), list):
            sp["scale"] = []

    # Name fallback so the renderer's `proposal["name"]` lookup never crashes.
    if "name" not in s:
        s["name"] = "(unnamed)"

    return s


# -- Public renderers -----------------------------------------------------


def render_preview(system: dict[str, Any]) -> str:
    """Render the single-system preview HTML.

    Delegates to the systemize preview machinery. Accepts both the systemize
    proposal shape and the validate-system schema; the adapter normalizes the
    latter before calling the renderer.
    """
    from harness.systemize import render_preview_html

    return render_preview_html(_normalize_for_systemize_renderer(system))


_COMPARISON_TMPL_HEAD = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="icon" href="data:,">
  <title>$title</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f8fafc;
      --cell-bg: #ffffff;
      --cell-border: #e2e8f0;
      --heading: #475569;
      --text: #0f172a;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        color-scheme: dark;
        --bg: #0b1220;
        --cell-bg: #111827;
        --cell-border: #1f2937;
        --heading: #94a3b8;
        --text: #e5e7eb;
      }
    }
    @media print {
      body { background: white; }
      .cell { break-inside: avoid; box-shadow: none; }
    }
    body {
      font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      margin: 0;
      padding: 24px;
      background: var(--bg);
      color: var(--text);
    }
    h1 { font-size: 18px; margin: 0 0 24px; }
    .grid {
      display: grid;
      grid-template-columns: repeat($n, minmax(0, 1fr));
      gap: 16px;
    }
    .cell {
      background: var(--cell-bg);
      border: 1px solid var(--cell-border);
      border-radius: 8px;
      padding: 16px;
      overflow: hidden;
    }
    .cell h2 {
      font-size: 13px;
      margin: 0 0 12px;
      color: var(--heading);
      text-transform: uppercase;
      letter-spacing: .05em;
    }
    .cell .preview {
      font-size: 12px;
      overflow: hidden;
    }
    .preview-frame {
      display: block;
      width: 100%;
      height: 760px;
      border: 0;
      background: var(--cell-bg);
    }
    @media (max-width: 900px) {
      body { padding: 16px; }
      .grid { grid-template-columns: 1fr; }
      .preview-frame { height: 640px; }
    }
  </style>
</head>
<body>
  <h1>$title</h1>
  <main class="grid">
"""

_COMPARISON_TMPL_FOOT = """\
  </main>
</body>
</html>
"""


def render_comparison(
    proposal: dict[str, Any],
    references: list[dict[str, Any]],
    labels: list[str],
    title: str = "System comparison",
) -> str:
    """Render a comparison HTML with one cell per system.

    The first column shows ``proposal``; subsequent columns show each entry
    in ``references``. ``labels`` must have one entry per system
    (1 + len(references)) and is HTML-escaped before embedding.

    The output is fully self-contained: no external CSS, no external scripts,
    no external fonts. Each cell's body is the relevant systemize preview
    content embedded inline.
    """
    if len(labels) != 1 + len(references):
        raise ValueError(
            f"labels has {len(labels)} entries; expected {1 + len(references)} "
            "(one for the proposal, one per reference)"
        )

    cells: list[str] = []
    all_systems = [proposal, *references]
    for system, label in zip(all_systems, labels, strict=True):
        document = render_preview(system)
        escaped_document = html.escape(document, quote=True)
        escaped_label = html.escape(label)
        cells.append(
            '<section class="cell">\n'
            f"  <h2>{escaped_label}</h2>\n"
            f'  <iframe class="preview-frame" title="{escaped_label} system preview" '
            f'srcdoc="{escaped_document}"></iframe>\n'
            "</section>"
        )

    head = _COMPARISON_TMPL_HEAD.replace("$title", html.escape(title)).replace(
        "$n", str(len(all_systems))
    )
    return head + "\n".join(cells) + "\n" + _COMPARISON_TMPL_FOOT


# -- Live mockups --------------------------------------------------------
#
# preview.html shows the system's tokens as chips, type samples, button
# samples — useful for verifying the values are right. But it doesn't tell
# the user what a real screen *using* the system looks like.
#
# These three renderers each produce a standalone HTML file demonstrating
# the system applied to a real UI archetype: a marketing landing page, a
# product dashboard, a form-heavy app. The hierarchy and content are fixed
# (so different systems are commensurable); only the tokens change.
#
# Each mockup pulls tokens from the system JSON and inlines them as CSS
# custom properties at the page root. No JavaScript. No external assets.
# Fits in one HTML file < 25KB. Opens with `file://` directly.


def _t(system: dict[str, Any], path: str, default: str = "") -> str:
    """Walk a `dot.delimited.path` into the system dict, returning default
    when any segment is absent. Used by the mockup renderers so the
    template-strings stay readable."""
    cur: Any = system
    for seg in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return default
        if cur is None:
            return default
    return str(cur) if not isinstance(cur, str) else cur


def _role_hex(system: dict[str, Any], role: str, fallback: str = "#000000") -> str:
    roles = (system.get("colors") or {}).get("roles") or {}
    spec = roles.get(role)
    if isinstance(spec, dict):
        h = spec.get("hex")
        if isinstance(h, str) and h:
            return html.escape(h)
    return fallback


def _size_for(system: dict[str, Any], role: str, fallback: int) -> int:
    for entry in (system.get("type") or {}).get("sizes") or []:
        if isinstance(entry, dict) and entry.get("role") == role:
            try:
                return int(entry.get("size_px") or fallback)
            except (TypeError, ValueError):
                return fallback
    return fallback


def _radius_for(system: dict[str, Any], role: str, fallback: int) -> int:
    for entry in (system.get("radii") or {}).get("scale") or []:
        if isinstance(entry, dict) and entry.get("role") == role:
            try:
                return int(entry.get("px") or fallback)
            except (TypeError, ValueError):
                return fallback
    return fallback


def _font_stack(system: dict[str, Any], role: str = "body") -> str:
    fonts = system.get("fonts") or {}
    candidate = fonts.get(role) if isinstance(fonts, dict) else None
    if not isinstance(candidate, str) or not candidate:
        candidate = (
            next(
                (
                    value
                    for value in (fonts.get("default"), fonts.get("primary"))
                    if isinstance(value, str) and value
                ),
                None,
            )
            if isinstance(fonts, dict)
            else None
        )
    from harness.systemize import _safe_font_stack

    return html.escape(_safe_font_stack(candidate))


def _mockup_css_vars(system: dict[str, Any]) -> str:
    """Emit a `:root { ... }` block exposing the system's load-bearing tokens
    as CSS custom properties. Mockups reference these by name."""
    parts: list[str] = [":root {"]
    parts.append(f"  --font-body: {_font_stack(system, 'body')};")
    parts.append(f"  --font-display: {_font_stack(system, 'display')};")
    parts.append(f"  --text-default: {_role_hex(system, 'text.default', '#0F172A')};")
    parts.append(f"  --text-subtle: {_role_hex(system, 'text.subtle', '#475569')};")
    parts.append(f"  --text-inverse: {_role_hex(system, 'text.inverse', '#FFFFFF')};")
    parts.append(f"  --surface-default: {_role_hex(system, 'surface.default', '#FFFFFF')};")
    parts.append(f"  --surface-subtle: {_role_hex(system, 'surface.subtle', '#F8FAFC')};")
    parts.append(f"  --surface-bold: {_role_hex(system, 'surface.bold', '#0F172A')};")
    parts.append(f"  --border-default: {_role_hex(system, 'border.default', '#E2E8F0')};")
    parts.append(f"  --border-focused: {_role_hex(system, 'border.focused', '#3B82F6')};")
    parts.append(f"  --primary: {_role_hex(system, 'primary', '#3B82F6')};")
    parts.append(f"  --body-px: {_size_for(system, 'body', 16)}px;")
    parts.append(f"  --h1-px: {_size_for(system, 'h1', 36)}px;")
    parts.append(f"  --h2-px: {_size_for(system, 'h2', 28)}px;")
    parts.append(f"  --h3-px: {_size_for(system, 'h3', 22)}px;")
    parts.append(f"  --radius-sm: {_radius_for(system, 'sm', 4)}px;")
    parts.append(f"  --radius-md: {_radius_for(system, 'md', 8)}px;")
    parts.append(f"  --radius-lg: {_radius_for(system, 'lg', 16)}px;")
    grid = (system.get("spacing") or {}).get("grid_px") or 4
    parts.append(f"  --grid: {int(grid)}px;")
    parts.append("}")
    return "\n".join(parts)


_MOCKUP_BASE_CSS = """\
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--font-body);
  font-size: var(--body-px);
  line-height: 1.5;
  color: var(--text-default);
  background: var(--surface-default);
  -webkit-font-smoothing: antialiased;
}
button {
  font: inherit;
  cursor: pointer;
  border: none;
}
a { color: inherit; text-decoration: none; }
h1, h2, h3, .nav-logo { font-family: var(--font-display); }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 3px solid var(--border-focused);
  outline-offset: 3px;
}
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}
.btn-primary {
  background: var(--primary);
  color: var(--text-inverse);
  padding: calc(var(--grid) * 3) calc(var(--grid) * 5);
  border-radius: var(--radius-md);
  font-weight: 600;
}
.btn-ghost {
  background: transparent;
  color: var(--text-default);
  padding: calc(var(--grid) * 3) calc(var(--grid) * 5);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
}
.muted { color: var(--text-subtle); }
"""


def render_landing_mockup(system: dict[str, Any]) -> str:
    """A marketing landing page: nav + hero + 3 feature cards + CTA strip.

    The structure is deliberately conventional so visual differences read
    as system differences, not content/layout differences.
    """
    name = html.escape(system.get("name") or "Untitled System")
    vars_block = _mockup_css_vars(system)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{name} — landing mockup</title>
<style>
{vars_block}
{_MOCKUP_BASE_CSS}
nav {{
  display: flex; align-items: center; justify-content: space-between;
  padding: calc(var(--grid) * 4) calc(var(--grid) * 8);
  border-bottom: 1px solid var(--border-default);
}}
.nav-logo {{ font-weight: 700; font-size: calc(var(--body-px) + 2px); }}
.nav-links {{ display: flex; gap: calc(var(--grid) * 6); }}
.hero {{
  padding: calc(var(--grid) * 24) calc(var(--grid) * 8);
  text-align: center;
}}
.hero h1 {{
  font-size: var(--h1-px); line-height: 1.18; margin: 0 0 calc(var(--grid) * 4);
  max-width: 14ch; margin-left: auto; margin-right: auto;
}}
.hero p {{
  font-size: calc(var(--body-px) + 4px); max-width: 50ch;
  margin: 0 auto calc(var(--grid) * 8);
}}
.hero .actions {{ display: flex; justify-content: center; gap: calc(var(--grid) * 3); }}
.features {{
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: calc(var(--grid) * 6);
  padding: calc(var(--grid) * 16) calc(var(--grid) * 8);
  background: var(--surface-subtle);
}}
.feature {{
  padding: calc(var(--grid) * 6);
  border-radius: var(--radius-lg);
  background: var(--surface-default);
  border: 1px solid var(--border-default);
}}
.feature h3 {{ font-size: var(--h3-px); margin: 0 0 calc(var(--grid) * 2); }}
.feature p {{ margin: 0; color: var(--text-subtle); }}
.cta-strip {{
  text-align: center;
  padding: calc(var(--grid) * 16) calc(var(--grid) * 8);
  background: var(--surface-bold);
  color: var(--text-inverse);
}}
.cta-strip h2 {{ font-size: var(--h2-px); margin: 0 0 calc(var(--grid) * 6); }}
.cta-strip .btn-ghost {{
  color: var(--text-inverse);
  border-color: var(--text-inverse);
}}
@media (max-width: 720px) {{
  nav {{ padding: calc(var(--grid) * 4); }}
  .nav-links {{ display: none; }}
  .hero {{ padding: calc(var(--grid) * 16) calc(var(--grid) * 5); text-align: left; }}
  .hero h1, .hero p {{ margin-left: 0; margin-right: 0; }}
  .hero .actions {{ justify-content: flex-start; flex-wrap: wrap; }}
  .features {{ grid-template-columns: 1fr; padding: calc(var(--grid) * 10) calc(var(--grid) * 5); }}
  .cta-strip {{ padding: calc(var(--grid) * 12) calc(var(--grid) * 5); }}
}}
</style>
</head>
<body>
<nav>
  <div class="nav-logo">{name}</div>
  <div class="nav-links muted">
    <a href="#features">Product</a><a href="#features">Pricing</a><a href="#features">Docs</a><a href="#cta">Sign in</a>
  </div>
  <button class="btn-primary">Get started</button>
</nav>
<section class="hero">
  <h1>The fastest way to ship reliable software</h1>
  <p class="muted">A landing mockup rendered with this system's tokens. Type
  scale, color roles, radii and spacing are all coming from system.json.</p>
  <div class="actions">
    <button class="btn-primary">Try it free</button>
    <button class="btn-ghost">Watch the demo</button>
  </div>
</section>
<section class="features" id="features">
  <article class="feature">
    <h3>Fast feedback</h3>
    <p>Real-time signals where you already work, with the noise filtered out.</p>
  </article>
  <article class="feature">
    <h3>Zero config</h3>
    <p>Drop in. The defaults are sensible, the customizations are obvious.</p>
  </article>
  <article class="feature">
    <h3>Self-hosted ready</h3>
    <p>Runs in your VPC. No outbound calls. Owns its own data plane.</p>
  </article>
</section>
<section class="cta-strip" id="cta">
  <h2>Ready when you are.</h2>
  <button class="btn-ghost">Read the docs</button>
</section>
</body>
</html>
"""


def render_dashboard_mockup(system: dict[str, Any]) -> str:
    """A product dashboard: sidebar + topbar + stat row + chart placeholder + table.

    Reveals the system's behavior under information density — where shadow
    grammar, radius scale, and chromatic restraint actually matter.
    """
    name = html.escape(system.get("name") or "Untitled System")
    vars_block = _mockup_css_vars(system)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{name} — dashboard mockup</title>
<style>
{vars_block}
{_MOCKUP_BASE_CSS}
.app {{
  display: grid;
  grid-template-columns: 240px 1fr;
  grid-template-rows: 56px 1fr;
  height: 100vh;
}}
.sidebar {{
  grid-row: 1 / 3;
  background: var(--surface-subtle);
  border-right: 1px solid var(--border-default);
  padding: calc(var(--grid) * 4) calc(var(--grid) * 3);
}}
.sidebar .brand {{
  font-weight: 700; margin-bottom: calc(var(--grid) * 6);
}}
.sidebar nav a {{
  display: block; padding: calc(var(--grid) * 2) calc(var(--grid) * 3);
  border-radius: var(--radius-md); color: var(--text-subtle);
  margin-bottom: calc(var(--grid) * 1);
}}
.sidebar nav a.active {{
  background: var(--surface-default); color: var(--text-default);
}}
.topbar {{
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 calc(var(--grid) * 6);
  border-bottom: 1px solid var(--border-default);
}}
.topbar input {{
  font: inherit;
  padding: calc(var(--grid) * 2) calc(var(--grid) * 3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--surface-default); color: var(--text-default);
  width: 320px;
}}
.main {{ padding: calc(var(--grid) * 8); overflow: auto; }}
.main h1 {{ font-size: var(--h2-px); margin: 0 0 calc(var(--grid) * 6); }}
.stat-row {{
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: calc(var(--grid) * 4); margin-bottom: calc(var(--grid) * 8);
}}
.stat {{
  padding: calc(var(--grid) * 5);
  border-radius: var(--radius-md);
  background: var(--surface-default);
  border: 1px solid var(--border-default);
}}
.stat .label {{ color: var(--text-subtle); font-size: calc(var(--body-px) - 2px); }}
.stat .value {{ font-size: var(--h3-px); margin-top: calc(var(--grid) * 1); }}
.chart {{
  height: 280px;
  border-radius: var(--radius-md);
  background: var(--surface-subtle);
  border: 1px solid var(--border-default);
  margin-bottom: calc(var(--grid) * 8);
  display: flex; align-items: end; gap: calc(var(--grid) * 1);
  padding: calc(var(--grid) * 4);
}}
.bar {{
  flex: 1; background: var(--primary); border-radius: var(--radius-sm) var(--radius-sm) 0 0;
  opacity: .9;
}}
table {{
  width: 100%; border-collapse: collapse;
  border-radius: var(--radius-md); overflow: hidden;
  border: 1px solid var(--border-default);
}}
th, td {{ text-align: left; padding: calc(var(--grid) * 3) calc(var(--grid) * 4); }}
thead {{ background: var(--surface-subtle); color: var(--text-subtle);
        font-size: calc(var(--body-px) - 2px); text-transform: uppercase;
        letter-spacing: .04em; }}
tbody tr {{ border-top: 1px solid var(--border-default); }}
.tag {{
  display: inline-block; padding: 2px calc(var(--grid) * 2);
  border-radius: var(--radius-sm); background: var(--surface-subtle);
  font-size: calc(var(--body-px) - 2px); color: var(--text-subtle);
}}
caption {{ padding: 0 0 calc(var(--grid) * 3); text-align: left; color: var(--text-subtle); }}
@media (max-width: 760px) {{
  .app {{ display: block; height: auto; min-height: 100vh; }}
  .sidebar {{ padding: calc(var(--grid) * 4); border-right: 0; border-bottom: 1px solid var(--border-default); }}
  .sidebar .brand {{ margin-bottom: calc(var(--grid) * 3); }}
  .sidebar nav {{ display: flex; gap: calc(var(--grid) * 1); overflow-x: auto; }}
  .sidebar nav a {{ flex: 0 0 auto; margin: 0; }}
  .topbar {{ gap: calc(var(--grid) * 3); padding: calc(var(--grid) * 3) calc(var(--grid) * 4); }}
  .topbar input {{ min-width: 0; width: 100%; }}
  .topbar .btn-primary {{ flex: 0 0 auto; }}
  .main {{ padding: calc(var(--grid) * 5) calc(var(--grid) * 4); }}
  .stat-row {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: calc(var(--grid) * 3); }}
  .chart {{ height: 210px; }}
  th:nth-child(2), td:nth-child(2) {{ display: none; }}
  th, td {{ padding: calc(var(--grid) * 3) calc(var(--grid) * 2); }}
}}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">{name}</div>
    <nav>
      <a class="active" href="#overview" aria-current="page">Overview</a>
      <a href="#projects">Projects</a>
      <a href="#decisions">Decisions</a>
      <a href="#reports">Reports</a>
      <a href="#research">Research</a>
      <a href="#settings">Settings</a>
    </nav>
  </aside>
  <header class="topbar">
    <label class="sr-only" for="workspace-search">Search workspace</label>
    <input id="workspace-search" type="search" placeholder="Search workspace" aria-label="Search workspace">
    <button class="btn-primary">New brief</button>
  </header>
  <main class="main" id="overview">
    <h1>Overview</h1>
    <section class="stat-row">
      <div class="stat"><div class="label">Active projects</div><div class="value">24</div></div>
      <div class="stat"><div class="label">Open decisions</div><div class="value">8</div></div>
      <div class="stat"><div class="label">Launch risks</div><div class="value">3</div></div>
      <div class="stat"><div class="label">On track</div><div class="value">91%</div></div>
    </section>
    <div class="chart" role="img" aria-label="Illustrative twelve-week delivery trend">
      <div class="bar" style="height: 35%"></div>
      <div class="bar" style="height: 52%"></div>
      <div class="bar" style="height: 41%"></div>
      <div class="bar" style="height: 68%"></div>
      <div class="bar" style="height: 74%"></div>
      <div class="bar" style="height: 60%"></div>
      <div class="bar" style="height: 82%"></div>
      <div class="bar" style="height: 91%"></div>
      <div class="bar" style="height: 88%"></div>
      <div class="bar" style="height: 96%"></div>
      <div class="bar" style="height: 78%"></div>
      <div class="bar" style="height: 85%"></div>
    </div>
    <table>
      <caption>Illustrative project activity</caption>
      <thead>
        <tr><th>Project</th><th>Stage</th><th>Status</th><th>Owner</th><th></th></tr>
      </thead>
      <tbody>
        <tr><td>Mobile onboarding</td><td>Build</td><td><span class="tag">On track</span></td><td>AR</td><td></td></tr>
        <tr><td>Billing migration</td><td>QA</td><td><span class="tag">At risk</span></td><td>JC</td><td></td></tr>
        <tr><td>Research archive</td><td>Discovery</td><td><span class="tag">On track</span></td><td>SO</td><td></td></tr>
        <tr><td>Workspace roles</td><td>Review</td><td><span class="tag">Blocked</span></td><td>MK</td><td></td></tr>
        <tr><td>Export reliability</td><td>Build</td><td><span class="tag">On track</span></td><td>TB</td><td></td></tr>
      </tbody>
    </table>
  </main>
</div>
</body>
</html>
"""


def render_form_mockup(system: dict[str, Any]) -> str:
    """A form-heavy app: account settings page with labels, helper text,
    radio group, validation hint. Demonstrates the system's behavior in
    text-dense, low-chrome environments."""
    name = html.escape(system.get("name") or "Untitled System")
    vars_block = _mockup_css_vars(system)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>{name} — form mockup</title>
<style>
{vars_block}
{_MOCKUP_BASE_CSS}
.page {{ max-width: 720px; margin: 0 auto; padding: calc(var(--grid) * 12) calc(var(--grid) * 6); }}
h1 {{ font-size: var(--h1-px); margin: 0 0 calc(var(--grid) * 8); }}
section {{
  background: var(--surface-default);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  padding: calc(var(--grid) * 8);
  margin-bottom: calc(var(--grid) * 8);
}}
section h2 {{
  font-size: var(--h3-px); margin: 0 0 calc(var(--grid) * 2);
}}
section .lead {{ color: var(--text-subtle); margin: 0 0 calc(var(--grid) * 6); }}
.field {{ display: grid; gap: calc(var(--grid) * 1); margin-bottom: calc(var(--grid) * 4); }}
label {{ font-weight: 500; }}
.help {{ color: var(--text-subtle); font-size: calc(var(--body-px) - 2px); }}
input[type="text"], input[type="email"], select, textarea {{
  font: inherit; padding: calc(var(--grid) * 3); width: 100%;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--surface-default); color: var(--text-default);
}}
input:focus, select:focus, textarea:focus {{
  outline: 2px solid var(--border-focused);
  outline-offset: 2px;
}}
.radio-group {{ display: grid; gap: calc(var(--grid) * 2); }}
.radio-row {{
  display: flex; align-items: flex-start; gap: calc(var(--grid) * 3);
  padding: calc(var(--grid) * 3);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
}}
.radio-row input {{ margin-top: 3px; }}
.actions {{
  display: flex; justify-content: flex-end; gap: calc(var(--grid) * 3);
  padding-top: calc(var(--grid) * 6);
  border-top: 1px solid var(--border-default);
}}
@media (max-width: 640px) {{
  .page {{ padding: calc(var(--grid) * 7) calc(var(--grid) * 4); }}
  section {{ padding: calc(var(--grid) * 5); border-radius: var(--radius-md); }}
  .actions {{ flex-wrap: wrap; }}
  .actions button {{ flex: 1 1 140px; }}
}}
</style>
</head>
<body>
<div class="page">
  <h1>Account settings</h1>

  <section>
    <h2>Profile</h2>
    <p class="lead">Information visible to others on your team.</p>
    <div class="field">
      <label for="name">Display name</label>
      <input id="name" type="text" autocomplete="name">
      <p class="help">Letters, numbers, and dashes. 2–32 characters.</p>
    </div>
    <div class="field">
      <label for="email">Work email</label>
      <input id="email" type="email" autocomplete="email">
      <p class="help">We'll send a verification link if you change this.</p>
    </div>
  </section>

  <section>
    <h2>Notifications</h2>
    <p class="lead">Choose how we contact you about activity in your workspace.</p>
    <div class="radio-group">
      <label class="radio-row">
        <input type="radio" name="freq" checked>
        <div>
          <div>Real-time</div>
          <p class="help">Email or push as events happen. Best for on-call rotations.</p>
        </div>
      </label>
      <label class="radio-row">
        <input type="radio" name="freq">
        <div>
          <div>Daily digest</div>
          <p class="help">One summary per workday at 9am local time.</p>
        </div>
      </label>
      <label class="radio-row">
        <input type="radio" name="freq">
        <div>
          <div>Weekly</div>
          <p class="help">Mondays. Good for reviewing rather than reacting.</p>
        </div>
      </label>
    </div>
  </section>

  <div class="actions">
    <button class="btn-ghost">Cancel</button>
    <button class="btn-primary">Save changes</button>
  </div>
</div>
</body>
</html>
"""


def render_system_md(system: dict[str, Any]) -> str:
    """Render a human-readable markdown summary of a system JSON.

    The output mirrors today's systemize markdown for the major sections
    (name, archetype, color roles, type scale, spacing) so a generated
    ``system.md`` reads like a hand-written reference.
    """
    name = system.get("name", "(unnamed)")
    arch = system.get("archetype", "(none)")
    lines: list[str] = []
    lines.append(f"# {name}")
    lines.append("")
    lines.append(f"**Archetype:** {arch}")
    lines.append("")

    decision = system.get("decision") if isinstance(system.get("decision"), dict) else {}
    evidence = system.get("evidence") if isinstance(system.get("evidence"), dict) else {}
    if decision:
        lines.append("## Executive decision")
        lines.append("")
        lines.append(f"- Status: **{decision.get('status', 'provisional')}**")
        lines.append(
            f"- Recommendation: **{decision.get('recommendation', 'revise-before-adoption')}**"
        )
        lines.append(f"- Confidence: **{decision.get('confidence', 'low')}**")
        for action in (decision.get("next_actions") or [])[:3]:
            lines.append(f"- Next: {action}")
        lines.append("")
    if evidence:
        lines.append("## Evidence basis")
        lines.append("")
        lines.append(f"- Source: `{evidence.get('source', 'unknown')}`")
        lines.append(f"- Elements examined: **{evidence.get('elements_examined', 0)}**")
        capture_scope = evidence.get("capture_scope")
        if isinstance(capture_scope, dict):
            lines.append(
                f"- Captures: **{capture_scope.get('succeeded', 0)}/"
                f"{capture_scope.get('requested', 0)}** succeeded"
            )
        lines.append("")

    roles = (system.get("colors") or {}).get("roles") or {}
    if roles:
        lines.append("## Color roles")
        lines.append("")
        lines.append("| Role | Hex |")
        lines.append("|---|---|")
        for role_name, spec in sorted(roles.items()):
            hex_ = spec.get("hex", "") if isinstance(spec, dict) else ""
            lines.append(f"| `{role_name}` | `{hex_}` |")
        lines.append("")

    type_sizes = (system.get("type") or {}).get("sizes") or []
    fonts = system.get("fonts") or {}
    if isinstance(fonts, dict) and fonts:
        body_font = fonts.get("body") or fonts.get("default") or fonts.get("primary")
        display_font = fonts.get("display") or body_font
        mono_font = fonts.get("mono")
        pairing_value = fonts.get("pairing")
        pairing: dict[str, Any] = pairing_value if isinstance(pairing_value, dict) else {}
        lines.append("## Typography pairing")
        lines.append("")
        if display_font:
            lines.append(f"- Display: **{display_font}**")
        if body_font:
            lines.append(f"- Body / interface: **{body_font}**")
        if mono_font:
            lines.append(f"- Code / data: **{mono_font}**")
        if pairing.get("rationale"):
            lines.append(f"- Rationale: {pairing['rationale']}")
        lines.append("")

    if type_sizes:
        lines.append("## Type scale")
        lines.append("")
        lines.append("| Role | Size (px) | Weight | Line-height |")
        lines.append("|---|---|---|---|")
        for s in type_sizes:
            if not isinstance(s, dict):
                continue
            lines.append(
                f"| `{s.get('role', '?')}` | {s.get('size_px', '?')} | "
                f"{s.get('weight', '?')} | {s.get('line_height', '?')} |"
            )
        lines.append("")

    sp = system.get("spacing") or {}
    if sp:
        lines.append("## Spacing")
        lines.append("")
        lines.append(f"- Grid: {sp.get('grid_px', '?')} px")
        scale_vals = sp.get("scale", []) or []
        lines.append(f"- Scale: {', '.join(str(x) for x in scale_vals)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
