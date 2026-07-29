"""URL safety helpers — SSRF guard and URL redaction.

The capture stage takes user-supplied targets, navigates to them via a headless
browser, and persists artefacts derived from them. Without guards, that's a
straightforward server-side request forgery: an attacker (or a careless caller)
can point us at AWS/GCP metadata endpoints, internal services, or localhost
admin panels.

`validate_target` is the gate. Call it before `page.goto`. Resolve the hostname
once via `socket.getaddrinfo` and reject any result whose IP lands in a
private/loopback/link-local/reserved range, plus a small explicit denylist of
known-bad targets (AWS, GCP, CGNAT).

`revalidate_target_at_request_time` is meant to be called from a Playwright
route handler on EVERY navigation request — including redirects and sub-
resources. It blocks unsafe literal redirects/subresources and detects a
rebound address when Python's resolver sees it. It does not pin Chromium's DNS
answer, so it cannot eliminate the same-request DNS-rebinding race.

`redact_url` is the storage hygiene helper. Tokens and session ids leak through
query strings constantly; strip them before writing the URL to disk.

Hardening notes (red-team pass 2026-05):
    - `socket.getaddrinfo` on macOS does NOT interpret leading zeros as
      octal (returns 177.0.0.1 for "0177.0.0.1"), but `socket.inet_aton`
      and historical browser URL parsers do (return 127.0.0.1). We must
      check both interpretations so we can't be tricked by ambiguous
      forms.
      Ref: https://book.hacktricks.wiki/en/pentesting-web/ssrf-server-side-request-forgery/url-format-bypass.html
    - DNS rebinding: a malicious DNS server can return a public IP for the
      validator's `getaddrinfo` call and a private IP for the browser's
      later resolution. A request-time route handler re-validates every URL
      and detects rebinding when its resolution sees the unsafe answer, but
      Python and Chromium resolutions are not pinned together. Treat capture
      of an actively hostile hostname as outside this local tool's boundary.
    - IPv4-mapped IPv6 (`::ffff:127.0.0.1`): Python's `ipaddress` already
      flags these via `is_loopback`/`is_private` on the v6 form, but we
      defensively also unwrap `ipv4_mapped` and re-classify the v4 form.
    - `is_unspecified` catches `0.0.0.0`; `is_multicast` and `is_reserved`
      together catch 224/4 and 255.255.255.255.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Collection
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse
from urllib.request import url2pathname

logger = logging.getLogger("keen")

NetworkOrigin = tuple[str, str, int]


# Explicit denylist of hostnames whose IP literal we might otherwise miss
# (e.g. metadata.google.internal resolves to a public-looking IP but is still
# a metadata endpoint we want to refuse).
_DENY_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

# Explicit IP/network denylist on top of the ipaddress library's classifiers.
# 169.254.169.254 is AWS/Azure/Oracle/DigitalOcean metadata; ipaddress.is_link_local
# already flags it, but we add it here so the rejection reason is precise.
_DENY_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("169.254.169.254/32"),
    # Container and pod credential endpoints used by AWS ECS/EKS.
    ipaddress.ip_network("169.254.170.2/32"),
    ipaddress.ip_network("169.254.170.23/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    # CGNAT — RFC 6598. ipaddress doesn't classify this as private by default.
    ipaddress.ip_network("100.64.0.0/10"),
)

_FILE_ASSET_SUFFIXES: dict[str, frozenset[str]] = {
    "stylesheet": frozenset({".css"}),
    "script": frozenset({".cjs", ".js", ".mjs"}),
    "image": frozenset(
        {
            ".apng",
            ".avif",
            ".bmp",
            ".gif",
            ".ico",
            ".jpeg",
            ".jpg",
            ".png",
            ".svg",
            ".webp",
        }
    ),
    "font": frozenset({".eot", ".otf", ".ttf", ".woff", ".woff2"}),
    "media": frozenset(
        {
            ".aac",
            ".flac",
            ".m4a",
            ".m4v",
            ".mp3",
            ".mp4",
            ".oga",
            ".ogg",
            ".ogv",
            ".wav",
            ".webm",
        }
    ),
    "texttrack": frozenset({".srt", ".vtt"}),
    "manifest": frozenset({".json", ".webmanifest"}),
    "other": frozenset({".ico"}),
}


def _local_file_path(target: str) -> tuple[Path, bool]:
    """Return a validated local path and whether the URL denotes a directory.

    This parser deliberately handles repeated percent-encoding and Windows
    separators before converting the path. Chromium applies the same kinds of
    normalization, so validating only the first decoded spelling would leave
    room for an encoded UNC path or traversal to cross the capture boundary.
    """
    parsed = urlparse(target)
    if (parsed.scheme or "").lower() != "file":
        raise ValueError("blocked target: expected a file:// URL")
    if parsed.netloc:
        raise ValueError("blocked target: remote file URL authorities are not allowed")
    raw_path = parsed.path
    if not raw_path:
        raise ValueError("blocked target: file URL must contain an absolute local path")
    decoded_path = raw_path
    # Repeated decoding catches nested encodings such as %255C%255Cserver.
    # Each changing pass strictly shortens the value, so the URL length is a
    # natural upper bound without an arbitrary decode-depth bypass.
    for _ in range(len(raw_path) + 1):
        next_path = unquote(decoded_path)
        if next_path == decoded_path:
            break
        decoded_path = next_path
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in decoded_path):
        raise ValueError("blocked target: file URL path contains control characters")
    normalized_path = decoded_path.replace("\\", "/")
    if not normalized_path.startswith("/") or normalized_path.startswith("//"):
        raise ValueError("blocked target: file URL must be an authority-free absolute local path")
    # Convert with the host platform's file-URL rules. Windows URLs spell a
    # drive path as /C:/..., while pathlib expects C:\...; using the URL
    # spelling directly would create a root-relative, drive-less path.
    return Path(url2pathname(normalized_path)), normalized_path.endswith("/")


def file_scope_root(target: str) -> Path:
    """Return the narrow local root an explicitly requested file URL grants.

    A file grants its containing directory so sibling assets continue to work.
    An explicit directory URL grants that directory, not its parent. Resolving
    the directory (rather than the target file) prevents a target-file symlink
    from silently widening the grant to the symlink destination's directory.
    """
    path, directory_hint = _local_file_path(target)
    try:
        if directory_hint or path.is_dir():
            return path.resolve(strict=False)
        return path.parent.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"blocked target: cannot resolve local file scope: {e}") from e


def file_document_path(target: str) -> Path:
    """Return the resolved path for an explicitly authorized file document."""
    path, _directory_hint = _local_file_path(target)
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"blocked target: cannot resolve local file path: {e}") from e


def _file_path_within_roots(target: str, roots: Collection[Path]) -> bool:
    path, _directory_hint = _local_file_path(target)
    try:
        resolved_path = path.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ValueError(f"blocked target: cannot resolve local file path: {e}") from e
    for root in roots:
        try:
            resolved_path.relative_to(root.resolve(strict=False))
        except (OSError, RuntimeError, ValueError):
            continue
        return True
    return False


def _validate_file_browser_request(
    target: str,
    *,
    documents: Collection[Path],
    resource_type: str | None,
    is_navigation_request: bool,
) -> None:
    """Keep documents exact while permitting ordinary publication assets."""
    path = file_document_path(target)
    normalized_documents = set()
    for document in documents:
        try:
            normalized_documents.add(document.resolve(strict=False))
        except (OSError, RuntimeError):
            continue

    if is_navigation_request or resource_type is None or resource_type == "document":
        if path not in normalized_documents:
            raise ValueError(
                "blocked target: file document was not explicitly requested for capture"
            )
        return

    suffixes = _FILE_ASSET_SUFFIXES.get(resource_type)
    if suffixes is None or path.suffix.lower() not in suffixes:
        raise ValueError(
            "blocked target: local file is not an allowed publication asset "
            f"for resource type '{resource_type}'"
        )


def _classify_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a reject reason string if `ip` falls in a blocked range, else None.

    Checks every dangerous category the ipaddress module knows about, plus
    our explicit network denylist. For IPv6 addresses that wrap a v4 address
    (e.g. `::ffff:127.0.0.1`), we also classify the unwrapped v4 form so the
    "::ffff:<private>" trick can't slip past a future ipaddress regression
    that stops flagging v4-mapped v6 as private/loopback.
    """
    if ip.is_loopback:
        return "loopback IP"
    if ip.is_link_local:
        return "link-local IP"
    if ip.is_private:
        return "private IP"
    if ip.is_reserved:
        return "reserved IP"
    if ip.is_multicast:
        return "multicast IP"
    if ip.is_unspecified:
        return "unspecified IP"
    # Defense-in-depth for IPv4-mapped IPv6 (e.g. ::ffff:10.0.0.1). In current
    # CPython, the v6 form's is_private/is_loopback already covers these, but
    # be explicit so a regression in ipaddress can't reopen the hole.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        v4_reason = _classify_ip(ip.ipv4_mapped)
        if v4_reason is not None:
            return f"IPv4-mapped IPv6 wrapping {v4_reason}"
    for net in _DENY_NETWORKS:
        try:
            if ip in net:
                return f"blocked network {net}"
        except TypeError:
            # Mismatched address family between ip and net; skip.
            continue
    return None


def _explicitly_denied_network(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """Return a permanently denied network, including through v4-mapped IPv6."""
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        candidates.append(ip.ipv4_mapped)
    for candidate in candidates:
        for network in _DENY_NETWORKS:
            try:
                if candidate in network:
                    return network
            except TypeError:
                continue
    return None


def _classify_non_internal_exception(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str | None:
    """Reject address classes that --allow-internal never authorizes."""
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        candidates.append(ip.ipv4_mapped)
    for candidate in candidates:
        if candidate.is_reserved:
            return "reserved IP"
        if candidate.is_multicast:
            return "multicast IP"
        if candidate.is_unspecified:
            return "unspecified IP"
    return None


def _normalize_host(host: str) -> str:
    """Lowercase, strip trailing dot, and IDNA-encode a hostname.

    Returns the ASCII (punycode) form so downstream comparisons against the
    ASCII-only `_DENY_HOSTS` set work even when the input contains Unicode.
    Falls back to the original `host` if IDNA encoding fails (e.g. for IP
    literals or hosts with characters outside the IDN profile).
    """
    h = host.lower().rstrip(".")
    if not h:
        return h
    # IP literals (v4 dotted, v4 short, v6 with colons or square brackets)
    # are not valid IDN input; leave them alone so the resolver / inet_aton
    # path handles them.
    if h.startswith("[") or ":" in h:
        return h
    try:
        # idna codec rejects empty labels and most non-DNS chars; treat any
        # failure as "leave the host as-is and let the resolver fail
        # naturally".
        return h.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return h


def network_origin(url: str, *, require_origin_only: bool = False) -> NetworkOrigin:
    """Return a canonical HTTP(S) origin suitable for exact policy matching."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise ValueError("blocked target: URL port is invalid") from None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("blocked target: allow-origin must be an absolute HTTP(S) origin")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("blocked target: URL userinfo is not allowed")
    if require_origin_only and (
        parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment
    ):
        raise ValueError("blocked target: allow-origin must not include a path, query, or fragment")
    host = _normalize_host(parsed.hostname)
    return scheme, host, port if port is not None else (80 if scheme == "http" else 443)


def _collect_ips_for_host(host: str) -> set[str]:
    """Return the union of every IP a host might resolve to.

    Combines three sources:
        - `socket.getaddrinfo(host)` — what the OS resolver returns.
        - `socket.inet_aton(host)` — BSD-style IPv4 literal parsing that
          interprets leading-0 (octal), 0x (hex), single-dword, and dotted
          short forms. Browsers historically used (and some legacy URL
          parsers still use) this exact parsing for the URL's host
          component, while macOS's `getaddrinfo` does NOT interpret octal
          forms the same way. That discrepancy is the bypass; resolving
          via BOTH sources and rejecting if EITHER lands in a blocked
          range closes it.
        - `socket.inet_pton(AF_INET6, host)` — IPv6 literal parsing for
          completeness; getaddrinfo usually handles this already.

    Raises ValueError if every source fails (host is utterly unresolvable).
    """
    ips: set[str] = set()
    resolver_error: Exception | None = None
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            sockaddr = info[4]
            # `getaddrinfo`'s sockaddr is `tuple[str, int]` for AF_INET and
            # `tuple[str, int, int, int]` for AF_INET6; mypy widens the union
            # to `str | int` for element 0. The first slot is always the host
            # string in both families, so a runtime isinstance narrows the
            # type without changing behavior. PEP 484: type narrowing via
            # isinstance.
            if not sockaddr:
                continue
            addr_obj = sockaddr[0]
            if not isinstance(addr_obj, str) or not addr_obj:
                continue
            # Strip IPv6 scope-id (e.g. fe80::1%en0); the IP portion is what
            # matters for classification.
            ips.add(addr_obj.split("%", 1)[0])
    except socket.gaierror as e:
        resolver_error = e

    # BSD inet_aton parses the V4 ambiguous forms (octal/hex/dword). This is
    # what closes the macOS-vs-browser disagreement on `0177.0.0.1`.
    try:
        packed = socket.inet_aton(host)
        ips.add(socket.inet_ntoa(packed))
    except (OSError, ValueError):
        pass

    # A four-part numeric host with leading zeroes is ambiguous across URL
    # parsers and operating-system resolvers. For example, Linux and BSD-style
    # parsers can read ``010.0.0.1`` as octal 8.0.0.1, while another resolver
    # may read it as decimal 10.0.0.1. Classify the explicit decimal reading as
    # well so a public interpretation cannot hide an RFC1918 destination.
    decimal_parts = host.split(".")
    if len(decimal_parts) == 4 and all(
        part and part.isascii() and part.isdigit() for part in decimal_parts
    ):
        decimal_values = [int(part, 10) for part in decimal_parts]
        if all(0 <= value <= 255 for value in decimal_values):
            ips.add(".".join(str(value) for value in decimal_values))

    # IPv6 literal — strip brackets if present.
    v6_candidate = host
    if v6_candidate.startswith("[") and v6_candidate.endswith("]"):
        v6_candidate = v6_candidate[1:-1]
    v6_candidate = v6_candidate.split("%", 1)[0]  # drop zone-id
    try:
        socket.inet_pton(socket.AF_INET6, v6_candidate)
        ips.add(v6_candidate)
    except (OSError, ValueError):
        pass

    if not ips:
        # Re-raise the original resolver error if we have it so the caller
        # gets a useful message.
        if resolver_error is not None:
            raise ValueError(
                f"blocked target: cannot resolve host '{host}': {resolver_error}"
            ) from resolver_error
        raise ValueError(f"blocked target: host '{host}' resolved to no addresses")
    return ips


def validate_target(
    target: str,
    *,
    allow_internal: bool = False,
    allow_file: bool = False,
    file_roots: Collection[Path] | None = None,
) -> str:
    """Validate a URL target for safe outbound navigation.

    Rejects:
        - schemes other than http/https (file:// allowed if `allow_file`)
        - hostnames that resolve to private/loopback/link-local/reserved IPs
        - explicit metadata/CGNAT denylist hits
        - IPv4 ambiguous forms (octal/hex/dword) where the BSD inet_aton
          interpretation lands in a blocked range — closes a macOS bypass
          where `getaddrinfo("0177.0.0.1")` returns 177.0.0.1 but a browser
          may parse the same host as 127.0.0.1

    Returns the original URL on success. Raises ValueError on rejection.

    Pass `allow_internal=True` to permit ordinary private, loopback, and
    link-local destinations. Explicit metadata/CGNAT networks plus
    reserved/multicast/unspecified addresses remain blocked. Pass
    `allow_file=True` to permit an explicitly requested `file://` URL. When
    validating browser requests, pass `file_roots` to restrict file access to
    the explicitly reviewed surface and its sibling assets.
    """
    if not target or not isinstance(target, str):
        raise ValueError("blocked target: empty or non-string target")
    if len(target) > 2_048:
        raise ValueError("blocked target: URL exceeds the 2048-character limit")
    if target != target.strip():
        raise ValueError("blocked target: URL contains leading or trailing whitespace")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
        raise ValueError("blocked target: URL contains control characters")

    parsed = urlparse(target)
    scheme = (parsed.scheme or "").lower()
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("blocked target: URL userinfo is not allowed")
    try:
        _port = parsed.port
    except ValueError:
        raise ValueError("blocked target: URL port is invalid") from None

    if scheme == "file":
        if not allow_file:
            raise ValueError("blocked target: file:// scheme requires --allow-file")
        _local_file_path(target)
        if file_roots is not None and not _file_path_within_roots(target, file_roots):
            raise ValueError("blocked target: file URL is outside the allowed local roots")
        return target

    if scheme not in {"http", "https"}:
        raise ValueError(f"blocked target: unsupported scheme '{scheme or '(empty)'}'")

    host = parsed.hostname
    if not host:
        raise ValueError("blocked target: missing hostname")

    host_lower = host.lower().rstrip(".")
    if host_lower in _DENY_HOSTS:
        raise ValueError(f"blocked target: host '{host_lower}' is denylisted")

    # IDNA-encode Unicode hostnames so the resolver sees a stable ASCII form
    # and our ASCII-only denylist can also match a Punycode equivalent of a
    # listed name.
    host_resolved = _normalize_host(host)
    if host_resolved in _DENY_HOSTS:
        raise ValueError(f"blocked target: host '{host_resolved}' is denylisted")

    # Resolve via multiple sources to cover macOS-vs-inet_aton discrepancies.
    ips = _collect_ips_for_host(host_resolved)

    for addr in ips:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise ValueError(f"blocked target: unparseable address '{addr}'") from None
        denied_network = _explicitly_denied_network(ip)
        if denied_network is not None:
            raise ValueError(
                f"blocked target: host '{host}' resolves to {addr} "
                f"(blocked network {denied_network})"
            )
        if allow_internal:
            reason = _classify_non_internal_exception(ip)
            if reason is not None:
                raise ValueError(f"blocked target: host '{host}' resolves to {addr} ({reason})")
            continue
        reason = _classify_ip(ip)
        if reason is not None:
            raise ValueError(f"blocked target: host '{host}' resolves to {addr} ({reason})")

    return target


def revalidate_target_at_request_time(
    url: str,
    *,
    allow_internal: bool = False,
    allow_file: bool = False,
    file_roots: Collection[Path] | None = None,
    file_documents: Collection[Path] | None = None,
    resource_type: str | None = None,
    is_navigation_request: bool = False,
    internal_origins: Collection[NetworkOrigin] | None = None,
) -> str:
    """Re-validate a URL captured at request time (redirects, sub-resources).

    Thin alias around `validate_target` so call-sites in the Playwright
    route handler read clearly: pre-flight validation happens once at CLI
    parse time and again before page.goto; request-time re-validation
    happens on every navigation hop the browser actually makes. Both run
    the SAME guard against the SAME blocklist so behaviour is consistent.

    Returns the URL on success, raises ValueError on rejection.
    """
    # An explicit top-level file target may be accepted with --allow-file, but
    # a browser request must always be tied to a precomputed local root. An
    # absent root set therefore fails closed for file:// while leaving HTTP(S)
    # behavior unchanged.
    request_file_roots = () if file_roots is None else file_roots
    request_allow_internal = False
    if allow_internal and urlparse(url).scheme.lower() in {"http", "https"}:
        origin = network_origin(url)
        request_allow_internal = origin in (() if internal_origins is None else internal_origins)
    validated = validate_target(
        url,
        allow_internal=request_allow_internal,
        allow_file=allow_file,
        file_roots=request_file_roots,
    )
    if urlparse(url).scheme.lower() == "file":
        _validate_file_browser_request(
            url,
            documents=() if file_documents is None else file_documents,
            resource_type=resource_type,
            is_navigation_request=is_navigation_request,
        )
    return validated


def redact_url(u: str) -> str:
    """Strip credentials, query string, and fragment from a URL.

    Used before persisting URLs to dom dumps or report fields — query strings
    routinely contain session tokens, OAuth codes, or signed parameters that
    have no business sitting in checked-in audit artefacts.
    """
    if not u or not isinstance(u, str):
        return ""
    try:
        parsed = urlparse(u)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme == "file":
        netloc = parsed.hostname or ""
    elif host:
        host_display = f"[{host}]" if ":" in host and not host.startswith("[") else host
        netloc = f"{host_display}:{port}" if port is not None else host_display
    else:
        netloc = ""
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
