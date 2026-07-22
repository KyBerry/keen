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
resources — to close the DNS-rebinding and redirect-bypass holes that a
one-shot pre-flight validation cannot cover.

`redact_url` is the storage hygiene helper. Tokens and session ids leak through
query strings constantly; strip them before writing the URL to disk.

Hardening notes (red-team pass 2026-05):
    - `socket.getaddrinfo` on macOS does NOT interpret leading zeros as
      octal (returns 177.0.0.1 for "0177.0.0.1"), but `socket.inet_aton`
      and historical browser URL parsers do (return 127.0.0.1). We must
      check both interpretations so we can't be tricked by ambiguous
      forms.
      Ref: https://book.hacktricks.wiki/en/pentesting-web/ssrf-server-side-request-forgery/url-format-bypass.html
    - DNS rebinding: a malicious DNS server returns a public IP for the
      validator's `getaddrinfo` call and a private IP for the browser's
      later resolution. The mitigation is a request-time route handler
      that re-validates `request.url` on every navigation hop. See
      `revalidate_target_at_request_time` and the `context.route` wiring
      in capture.py.
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
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("keen")


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
    # CGNAT — RFC 6598. ipaddress doesn't classify this as private by default.
    ipaddress.ip_network("100.64.0.0/10"),
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

    Pass `allow_internal=True` to skip IP-range checks (still rejects scheme
    abuse). Pass `allow_file=True` to permit `file://` URLs.
    """
    if not target or not isinstance(target, str):
        raise ValueError("blocked target: empty or non-string target")

    parsed = urlparse(target)
    scheme = (parsed.scheme or "").lower()

    if scheme == "file":
        if not allow_file:
            raise ValueError("blocked target: file:// scheme requires --allow-file")
        return target

    if scheme not in {"http", "https"}:
        raise ValueError(f"blocked target: unsupported scheme '{scheme or '(empty)'}'")

    host = parsed.hostname
    if not host:
        raise ValueError("blocked target: missing hostname")

    host_lower = host.lower().rstrip(".")
    if host_lower in _DENY_HOSTS:
        raise ValueError(f"blocked target: host '{host_lower}' is denylisted")

    if allow_internal:
        return target

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
        reason = _classify_ip(ip)
        if reason is not None:
            raise ValueError(f"blocked target: host '{host}' resolves to {addr} ({reason})")

    return target


def revalidate_target_at_request_time(
    url: str,
    *,
    allow_internal: bool = False,
    allow_file: bool = False,
) -> str:
    """Re-validate a URL captured at request time (redirects, sub-resources).

    Thin alias around `validate_target` so call-sites in the Playwright
    route handler read clearly: pre-flight validation happens once at CLI
    parse time and again before page.goto; request-time re-validation
    happens on every navigation hop the browser actually makes. Both run
    the SAME guard against the SAME blocklist so behaviour is consistent.

    Returns the URL on success, raises ValueError on rejection.
    """
    return validate_target(url, allow_internal=allow_internal, allow_file=allow_file)


def redact_url(u: str) -> str:
    """Strip query string and fragment from a URL.

    Used before persisting URLs to dom dumps or report fields — query strings
    routinely contain session tokens, OAuth codes, or signed parameters that
    have no business sitting in checked-in audit artefacts.
    """
    if not u or not isinstance(u, str):
        return ""
    try:
        parsed = urlparse(u)
    except ValueError:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
