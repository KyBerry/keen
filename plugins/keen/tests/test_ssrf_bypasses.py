"""Adversarial / red-team bypass tests for the SSRF guard.

Each test exercises one of the canonical SSRF URL-format tricks. After the
2026-05 hardening pass, the validator (and the request-time route handler in
capture.py that calls `revalidate_target_at_request_time`) must REJECT each
input below.

Original vulnerability research and references:
    - https://book.hacktricks.wiki/en/pentesting-web/ssrf-server-side-request-forgery/url-format-bypass.html
    - https://www.rfc-editor.org/rfc/rfc6890 (special-purpose IP registries)
    - https://www.rfc-editor.org/rfc/rfc6598 (CGNAT 100.64.0.0/10)
    - https://datatracker.ietf.org/doc/html/draft-ietf-6man-ipv6-zone-id (zone ids)
    - https://chromium.googlesource.com/chromium/src/+/main/url/url_canon_ip.cc
      (browser IPv4 parsing — historically tolerant of leading-0 / hex /
      shorthand forms which is the exact bypass for macOS `getaddrinfo`).

The tests are written so that BEFORE hardening they would fail (i.e. the
validator would accept the URL); AFTER hardening they pass. Inputs that
the unhardened code already blocked are marked "already-blocked" in the
docstring — we still keep tests so any future regression is caught.
"""

from __future__ import annotations

import socket

import pytest

from harness._urlsafe import (
    revalidate_target_at_request_time,
    validate_target,
)

# --- helpers --------------------------------------------------------------


def _fake_resolver(*ips: str):
    """Build a `getaddrinfo` replacement that returns the given IPs.

    Used to simulate DNS-controlled scenarios (rebinding, attacker-owned
    domain serving a private IP, redirect targets resolved through DNS).
    """

    def _fake(host: str, port, *args, **kwargs) -> list[tuple]:
        family = socket.AF_INET
        return [(family, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in ips]

    return _fake


def _expect_block(url: str, pattern: str = "blocked target") -> None:
    """Assert both the pre-flight and request-time guards reject `url`."""
    with pytest.raises(ValueError, match=pattern):
        validate_target(url)
    with pytest.raises(ValueError, match=pattern):
        revalidate_target_at_request_time(url)


# =========================================================================
# Vector 1: DNS rebinding (TOCTOU between validator resolve and goto resolve)
# =========================================================================
#
# A request-time check detects the unsafe answer when Python's resolver sees
# it. It does not pin Chromium's independent DNS result, so this is a
# mitigation regression rather than proof that all rebinding races are closed.
#
# We can't end-to-end exercise a real malicious DNS server in a unit test,
# so we simulate the "second resolution returns a private IP" half of the
# attack by stubbing the resolver. The route handler in capture.py calls
# `revalidate_target_at_request_time` which performs THE SAME getaddrinfo
# call we stub here — so a rebound IP visible to that check gets rejected.


def test_dns_rebinding_secondary_resolve_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a host re-resolves to a private IP at request time, reject.

    Simulates the malicious DNS server returning 10.0.0.5 for the second
    lookup (the one the browser would have done). The route handler invokes
    `revalidate_target_at_request_time`, which runs getaddrinfo again. Here
    we stub that resolve to return the post-rebind private IP.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver("10.0.0.5"))
    with pytest.raises(ValueError, match=r"private IP|blocked network"):
        revalidate_target_at_request_time("http://rebind.example.com/")


def test_dns_rebinding_to_metadata_endpoint_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver("169.254.169.254"))
    with pytest.raises(
        ValueError,
        match=r"link-local|blocked network|169\.254",
    ):
        revalidate_target_at_request_time("http://rebind.example.com/")


# =========================================================================
# Vector 2: IPv4 alternative representations (octal/hex/dword/shorthand)
# =========================================================================


def test_octal_ipv4_0177_blocked() -> None:
    """`0177.0.0.1` — REAL BYPASS on macOS before hardening.

    macOS `getaddrinfo` returns 177.0.0.1 (public) for "0177.0.0.1"; Chrome's
    URL canonicalizer (and `socket.inet_aton`) returns 127.0.0.1 (loopback).
    The unhardened validator trusted only getaddrinfo, so it accepted the
    URL. The hardened validator also runs `inet_aton` and rejects when EITHER
    interpretation lands in a blocked range.
    """
    _expect_block(
        "http://0177.0.0.1/",
        pattern="loopback IP|blocked target",
    )


def test_octal_ipv4_padded_octets_blocked() -> None:
    """`0177.000.000.001` — same family as 0177.0.0.1."""
    _expect_block(
        "http://0177.000.000.001/",
        pattern="loopback IP|blocked target",
    )


def test_hex_ipv4_blocked() -> None:
    """`0x7f.0.0.1` — already-blocked; macOS resolves to 127.0.0.1."""
    _expect_block("http://0x7f.0.0.1/", pattern="loopback IP")


def test_hex_dword_ipv4_blocked() -> None:
    """`0x7f000001` — single 32-bit dword as hex; already-blocked."""
    _expect_block("http://0x7f000001/", pattern="loopback IP")


def test_octal_dword_ipv4_blocked() -> None:
    """`017700000001` — octal single dword. inet_aton interprets, getaddrinfo
    on macOS also (because no dots = treated as a single number)."""
    _expect_block("http://017700000001/", pattern="loopback IP")


def test_decimal_dword_ipv4_blocked() -> None:
    """`2130706433` = 0x7f000001 = 127.0.0.1. Already-blocked."""
    _expect_block("http://2130706433/", pattern="loopback IP")


def test_short_ipv4_blocked() -> None:
    """`127.1` -> 127.0.0.1. Already-blocked."""
    _expect_block("http://127.1/", pattern="loopback IP")


def test_trailing_dot_localhost_blocked() -> None:
    """`localhost.` — fully-qualified form of localhost. Already-blocked."""
    _expect_block("http://localhost./", pattern="loopback IP")


def test_octal_private_ipv4_blocked() -> None:
    """`010.0.0.1` resolves either to 10.0.0.1 (resolver) or 8.0.0.1
    (inet_aton). Both are flagged: 10.0.0.1 is RFC1918 private; 8.0.0.1
    is not but the inet_aton form returns a different (still public) IP,
    so the actual test here is: at least one path must reject. We accept
    "private IP" reason for the macOS resolver path."""
    _expect_block("http://010.0.0.1/", pattern="private IP")


def test_octal_aws_metadata_blocked() -> None:
    """`0251.0xfe.0xa9.0xfe` — wild mix that BSD inet_aton would canonicalize
    to 169.254.169.254. The defense via inet_aton ensures this is caught
    even if a resolver normalizes it differently."""
    # 0251 = 169, 0xfe = 254, 0xa9 = 169, 0xfe = 254
    _expect_block(
        "http://0251.0xfe.0xa9.0xfe/",
        pattern="link-local|blocked network|169.254",
    )


# =========================================================================
# Vector 3: IPv4-mapped IPv6
# =========================================================================


def test_ipv4_mapped_loopback_blocked() -> None:
    """`[::ffff:127.0.0.1]` — IPv4-mapped IPv6 loopback. Already-blocked by
    Python's `is_loopback` on the IPv6 form, but we also classify the
    unwrapped v4 form defensively."""
    _expect_block(
        "http://[::ffff:127.0.0.1]/",
        pattern="loopback IP|IPv4-mapped",
    )


def test_ipv4_mapped_private_blocked() -> None:
    """`[::ffff:10.0.0.1]` — v4-mapped private."""
    _expect_block(
        "http://[::ffff:10.0.0.1]/",
        pattern="private IP|IPv4-mapped",
    )


def test_ipv4_mapped_aws_metadata_blocked() -> None:
    """`[::ffff:169.254.169.254]` — v4-mapped AWS metadata IP."""
    _expect_block(
        "http://[::ffff:169.254.169.254]/",
        pattern="link-local|blocked network|169.254|IPv4-mapped",
    )


# =========================================================================
# Vector 4: IPv6 link-local with zone id
# =========================================================================


def test_ipv6_link_local_with_zone_id_blocked() -> None:
    """`[fe80::1%25en0]` — URL-encoded zone id `%25` -> `%`. Already-blocked
    (fe80::/10 is link-local; we strip the zone id before classifying)."""
    _expect_block(
        "http://[fe80::1%25en0]/",
        pattern="link-local IP",
    )


# =========================================================================
# Vector 5: IPv6 abbreviation games
# =========================================================================


def test_ipv6_loopback_compressed_blocked() -> None:
    """`[::1]` — already-blocked."""
    _expect_block("http://[::1]/", pattern="loopback IP")


def test_ipv6_loopback_expanded_blocked() -> None:
    """`[0:0:0:0:0:0:0:1]` — same IP as ::1, just unabbreviated.
    Already-blocked."""
    _expect_block("http://[0:0:0:0:0:0:0:1]/", pattern="loopback IP")


# =========================================================================
# Vector 6: 0.0.0.0 unspecified
# =========================================================================


def test_unspecified_ipv4_blocked() -> None:
    """`0.0.0.0` — `is_unspecified` on Linux maps to "this host" semantics."""
    _expect_block(
        "http://0.0.0.0/",
        pattern="unspecified IP|private IP",
    )


def test_unspecified_ipv6_blocked() -> None:
    """`[::]` — IPv6 unspecified.

    Note: Python classifies `::` as `is_private` and `is_reserved` too
    (it's in 0::/8 which is reserved by IANA), so the rejection reason
    may be "private IP" or "reserved IP" depending on check order. We
    accept either — the important thing is the URL is rejected.
    """
    _expect_block(
        "http://[::]/",
        pattern="unspecified IP|reserved IP|private IP",
    )


# =========================================================================
# Vector 7: Multicast / broadcast
# =========================================================================


def test_multicast_ipv4_blocked() -> None:
    """`224.0.0.1` — multicast. Already-blocked via `is_multicast`."""
    _expect_block("http://224.0.0.1/", pattern="multicast IP")


def test_broadcast_ipv4_blocked() -> None:
    """`255.255.255.255` — limited broadcast. Already-blocked via
    `is_reserved` / `is_private` on this special address."""
    _expect_block(
        "http://255.255.255.255/",
        pattern="reserved IP|private IP",
    )


# =========================================================================
# Vector 8: IDN homoglyph / punycode
# =========================================================================


def test_idn_homoglyph_blocks_when_resolves_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Unicode homoglyph host that happens to be registered and points at
    a private IP must be rejected. We stub the resolver to simulate that
    registration."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver("10.0.0.5"))
    # Cyrillic U+043E replaces ASCII 'o' in 'localhost' — that's the point
    # of the test, so silence the ambiguous-character lint on the string.
    with pytest.raises(ValueError, match="private IP"):
        validate_target("http://lоcalhost.example/")  # noqa: RUF001


def test_idn_punycode_blocks_when_resolves_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same domain, presented in its Punycode form. Must also be blocked."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver("10.0.0.5"))
    with pytest.raises(ValueError, match="private IP"):
        validate_target("http://xn--lcalhost-nbh.example/")


# =========================================================================
# Vector 9: HTTP redirect chain (validator only checks initial URL)
# =========================================================================
#
# A real redirect goes through Playwright's route handler at request time.
# We simulate the "second URL" by calling the same revalidator the route
# handler uses, with the redirected URL.


def test_redirect_to_metadata_blocked_at_request_time() -> None:
    """A 302 from a benign domain to 169.254.169.254 must be caught by the
    request-time revalidator that the route handler calls."""
    with pytest.raises(ValueError, match=r"link-local|blocked network|169\.254"):
        revalidate_target_at_request_time("http://169.254.169.254/latest/meta-data/")


def test_redirect_to_loopback_blocked_at_request_time() -> None:
    with pytest.raises(ValueError, match="loopback"):
        revalidate_target_at_request_time("http://127.0.0.1/admin")


def test_redirect_to_rfc1918_blocked_at_request_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver("192.168.1.1"))
    with pytest.raises(ValueError, match="private IP"):
        revalidate_target_at_request_time("http://intra.example.com/")


# =========================================================================
# Vector 10: Userinfo trick
# =========================================================================


def test_userinfo_does_not_mask_blocked_host() -> None:
    """`http://169.254.169.254@example.com/` — the IP is userinfo, not the
    host. urlparse correctly extracts 'example.com' as the hostname. We
    verify the validator does NOT get confused by the userinfo."""
    # example.com is the real host. Stub it to a public IP so the test is
    # deterministic (don't depend on real DNS).
    # Using monkeypatch fixture indirectly via direct mock here:
    import unittest.mock as _mock

    with (
        _mock.patch.object(
            socket,
            "getaddrinfo",
            return_value=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("93.184.216.34", 0),
                )
            ],
        ),
        pytest.raises(ValueError, match="userinfo"),
    ):
        validate_target("http://169.254.169.254@example.com/")


def test_userinfo_with_blocked_real_host_blocked() -> None:
    """`http://anything@127.0.0.1/` — the userinfo is the decoy; the real
    host is loopback. Must block."""
    _expect_block(
        "http://decoy@127.0.0.1/",
        pattern="userinfo",
    )


# =========================================================================
# Vector 11: Embedded credentials with ?/#
# =========================================================================


def test_query_string_with_blocked_ip_does_not_confuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`http://example.com?169.254.169.254=x` — the IP is a query parameter,
    not the host. Must NOT be blocked just because the IP appears in the URL."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_resolver("93.184.216.34"),
    )
    result = validate_target("http://example.com/?target=169.254.169.254")
    assert "example.com" in result


def test_fragment_with_blocked_ip_does_not_confuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`http://example.com#169.254.169.254` — fragment, not host."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_resolver("93.184.216.34"),
    )
    result = validate_target("http://example.com/#169.254.169.254")
    assert "example.com" in result


# =========================================================================
# Vector 12: CRLF injection in host
# =========================================================================


def test_crlf_in_host_rejected() -> None:
    """`http://localhost%0d%0a.../` — % escapes in hostname. Python's URL
    parser percent-decodes and the resolver fails on the resulting non-DNS
    characters. Either way it must be rejected before any goto."""
    with pytest.raises(ValueError):
        validate_target("http://localhost%0d%0aSet-Cookie: x=y/")


def test_raw_crlf_in_host_rejected() -> None:
    """Raw `\\r\\n` bytes injected into hostname — must reject."""
    with pytest.raises(ValueError):
        validate_target("http://localhost\r\nSet-Cookie: x=y/")


# =========================================================================
# Sanity: benign public hosts continue to work
# =========================================================================


def test_benign_public_host_still_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hardening must not block normal public hosts."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_resolver("93.184.216.34"),
    )
    assert validate_target("https://example.com/") == "https://example.com/"


def test_benign_subresource_still_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CDN sub-resource on a public host must pass the request-time
    revalidator (covers the route-handler benign path)."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_resolver("93.184.216.34"),
    )
    assert (
        revalidate_target_at_request_time("https://cdn.example.com/static/main.css")
        == "https://cdn.example.com/static/main.css"
    )


def test_https_with_path_query_fragment_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_resolver("93.184.216.34"),
    )
    url = "https://example.com/path/to?token=x#frag"
    assert validate_target(url) == url
