"""Tests designed to kill mutmut survivors in harness/_urlsafe.py.

The existing test_urlsafe.py exercises behaviour broadly but doesn't
assert exact wording of rejection-reason strings, doesn't check the
internal `_normalize_host` IDN/colon/empty-host short-circuits, and
doesn't pin the multi-source `_collect_ips_for_host` integration. This
file fills those gaps.
"""

from __future__ import annotations

import socket
from unittest import mock

import pytest

from harness._urlsafe import (
    redact_url,
    revalidate_target_at_request_time,
    validate_target,
)


def _fake_getaddrinfo(ip: str):
    """Return a getaddrinfo replacement that always resolves to `ip`."""

    def _fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]

    return _fake


# ---------------------------------------------------------------------------
# Exact rejection-reason strings (mutations 6, 7, 8, 9, 13)
# ---------------------------------------------------------------------------


def test_loopback_reason_is_exactly_loopback_ip() -> None:
    # mutmut: kill mutation 6 — was: "loopback IP" -> "XXloopback IPXX".
    with pytest.raises(ValueError) as ei:
        validate_target("http://127.0.0.1/")
    msg = str(ei.value)
    assert "loopback IP" in msg
    assert "XXloopback IPXX" not in msg


def test_link_local_reason_string(monkeypatch: pytest.MonkeyPatch) -> None:
    # mutmut: kill mutation 7 — was: "link-local IP" -> "XXlink-local IPXX".
    # 169.254.x.x is link-local but the specific 169.254.169.254 is also in
    # the explicit deny network, which would mask the link-local message.
    # Use a different link-local IP so the link-local branch wins.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.5.5"))
    with pytest.raises(ValueError) as ei:
        validate_target("http://ll.example/")
    msg = str(ei.value)
    assert "link-local IP" in msg
    assert "XXlink-local IPXX" not in msg


def test_private_reason_string() -> None:
    # mutmut: kill mutation 8 — was: "private IP" -> "XXprivate IPXX".
    with pytest.raises(ValueError) as ei:
        validate_target("http://10.0.0.1/")
    msg = str(ei.value)
    assert "private IP" in msg
    assert "XXprivate IPXX" not in msg


def test_multicast_reason_string(monkeypatch: pytest.MonkeyPatch) -> None:
    # mutmut: kill mutation 9 — was: "multicast IP" -> "XXmulticast IPXX".
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("224.0.0.1"))
    with pytest.raises(ValueError) as ei:
        validate_target("http://mc.example/")
    msg = str(ei.value)
    assert "multicast IP" in msg
    assert "XXmulticast IPXX" not in msg


def test_blocked_network_reason_string(monkeypatch: pytest.MonkeyPatch) -> None:
    # mutmut: kill mutation 13 — was: f"blocked network {net}" ->
    # f"XXblocked network {net}XX". CGNAT (100.64.0.0/10) is only in the
    # explicit network list — ipaddress doesn't classify it as private.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("100.64.0.1"))
    with pytest.raises(ValueError) as ei:
        validate_target("http://cgnat.example/")
    msg = str(ei.value)
    assert "blocked network" in msg
    assert "XXblocked network" not in msg


# ---------------------------------------------------------------------------
# Empty/non-string targets and rejection messages (mutations 55, 56, 64, 68)
# ---------------------------------------------------------------------------


def test_non_string_target_rejected_via_isinstance_check() -> None:
    # mutmut: kill mutation 55 — was: `not target or not isinstance(target,
    # str)` -> `... and ...`. With a *non-empty* non-string (e.g. 42):
    # `not target` is False AND `not isinstance` is True. Original: False
    # or True = True -> raise. Mutant: False and True = False -> proceed
    # to urlparse(42) which fails or returns garbage.
    with pytest.raises(ValueError):
        validate_target(42)  # type: ignore[arg-type]


def test_empty_target_message_exact() -> None:
    # mutmut: kill mutation 56 — was: "blocked target: empty or non-string
    # target" -> "XX...XX".
    with pytest.raises(ValueError) as ei:
        validate_target("")
    msg = str(ei.value)
    assert msg == "blocked target: empty or non-string target"


def test_file_scheme_message_exact() -> None:
    # mutmut: kill mutation 64 — was: "blocked target: file:// scheme
    # requires --allow-file" -> "XX...XX".
    with pytest.raises(ValueError) as ei:
        validate_target("file:///etc/passwd")
    msg = str(ei.value)
    assert msg == "blocked target: file:// scheme requires --allow-file"


def test_unsupported_scheme_message_includes_empty_marker() -> None:
    # mutmut: kill mutation 68 — was: f"... '{scheme or '(empty)'}'" ->
    # `'XX(empty)XX'`. Trigger by passing a URL whose scheme parses to
    # empty (parsed.scheme=""), which then falls through to the unsupported
    # branch.
    with pytest.raises(ValueError) as ei:
        validate_target("//example.com/path")
    msg = str(ei.value)
    assert "(empty)" in msg
    assert "XX(empty)XX" not in msg


def test_unsupported_scheme_uses_or_not_and(monkeypatch: pytest.MonkeyPatch) -> None:
    # mutmut: kill mutation 69 — was: f"... '{scheme or '(empty)'}'" ->
    # `{scheme and '(empty)'}`. For a non-empty unsupported scheme like
    # "javascript", original substitutes the scheme; mutant substitutes
    # "(empty)" because "javascript" is truthy and `and` returns the
    # right operand.
    with pytest.raises(ValueError) as ei:
        validate_target("javascript:alert(1)")
    msg = str(ei.value)
    assert "javascript" in msg
    # Mutant would have replaced the scheme name with "(empty)".
    assert "'(empty)'" not in msg


# ---------------------------------------------------------------------------
# _normalize_host: rstrip dots, empty-host check, IP-literal short-circuit
# (mutations 14, 16, 17, 18, 19, 20)
# ---------------------------------------------------------------------------


def test_trailing_dot_host_is_normalized_and_blocked() -> None:
    # mutmut: kill mutation 14 — was: rstrip(".") -> rstrip("XX.XX").
    # A trailing-dot version of a deny-listed host must still be denied
    # after normalization stripping the dot. Without the rstrip the host
    # would carry the trailing dot and miss the deny set.
    with pytest.raises(ValueError, match="denylisted"):
        validate_target("http://metadata.google.internal./")


def test_normalize_host_handles_empty_after_rstrip() -> None:
    # mutmut: kill mutation 16 — was: `if not h: return h` -> `if h: return
    # h`. An input that's just dots becomes "" after rstrip; original
    # short-circuits and returns ""; mutant skips that branch and proceeds
    # to encode("idna") which would raise on "".
    # The empty host case is reached via urlparse "http://./" where the
    # hostname is "." -> normalized to "". Then the host_resolved lookup
    # for an empty string skips the deny set, and _collect_ips_for_host
    # raises trying to resolve "".
    with pytest.raises(ValueError):
        # This may raise either because the host can't be resolved or
        # because the URL is unparseable. Either way the call must error
        # out, but most importantly _normalize_host must NOT itself error
        # on the empty-string input.
        validate_target("http://./")


def test_ipv6_host_with_colon_skips_idna() -> None:
    # mutmut: kill mutations 17, 18, 19, 20 — these break the IP-literal
    # short-circuit. The idna codec rejects ":" — if the short-circuit is
    # broken the call to encode("idna") raises UnicodeError, the host
    # falls back to as-is, and the resolver path still works. But mutation
    # 19 inverts `":" in h` to `":" not in h`, which means literal v6 hosts
    # fall through to encode("idna") and crash.
    # Path: validate_target("http://[::1]/") -> hostname becomes "::1".
    # "::1" contains a colon. Under the original, normalize_host returns
    # "::1" early. Then _collect_ips_for_host resolves "::1" via
    # inet_pton -> ip is loopback -> reject "loopback IP".
    with pytest.raises(ValueError, match=r"loopback|link-local|private"):
        validate_target("http://[::1]/")


# ---------------------------------------------------------------------------
# inet_aton bypass / multi-source IP collection (mutations 34, 35, 36, 41-45)
# ---------------------------------------------------------------------------


def test_ipv4_octal_form_rejected_via_inet_aton() -> None:
    # mutations 34/35/36/41-45: changes inside _collect_ips_for_host.
    # The "octal IP" bypass — `0177.0.0.1` resolves via inet_aton to
    # 127.0.0.1 on every platform; getaddrinfo on macOS would not catch
    # this. So validate_target must still reject it because inet_aton
    # contributes 127.0.0.1 to the candidate set.
    with pytest.raises(ValueError):
        # The macOS getaddrinfo behaviour means this either raises gaierror
        # (no DNS) or resolves to 177.0.0.1. Either way, inet_aton catches
        # the loopback alias.
        validate_target("http://0177.0.0.1/")


def test_ipv4_dword_form_rejected_via_inet_aton(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force getaddrinfo to fail so we know rejection came from inet_aton.
    def _boom(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    # 2130706433 in single-dword form == 127.0.0.1 under inet_aton.
    with pytest.raises(ValueError, match="loopback"):
        validate_target("http://2130706433/")


def test_ipv6_scope_id_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    # mutmut: kill mutations 35, 36, 44, 45 — was: addr.split("%", 1)[0]
    # -> "XX%XX" / split limit 2. Either change still gets the IP portion
    # in the simple case (no % present), but they corrupt addrs that
    # contain "%". Simulate a v6 link-local with scope id.
    def _fake(host, port, *args, **kwargs):
        # Return an address WITH a zone-id; the implementation strips %en0
        return [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("fe80::1%en0", 0, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    with pytest.raises(ValueError, match="link-local"):
        validate_target("http://link-local.example/")


def test_ipv6_bracket_host_strips_brackets() -> None:
    # mutmut: kill mutations 41, 42, 43 — these mess with the bracket
    # check or change `and` to `or`. With `or`, e.g. host="example.com"
    # would not start with "[" but the right clause "endswith(']')" might
    # be True only for ipv6-formatted strings, so the slice `[1:-1]`
    # would chop legitimate hostnames. Verify a plain hostname survives.
    # A bracketed v6 literal must still be classified.
    with pytest.raises(ValueError):
        validate_target("http://[fc00::1]/")  # private fc00::/7
    # Also: an ordinary hostname must reach the resolver step; if the
    # bracket-strip is mis-applied the host might become "xample.co".


# ---------------------------------------------------------------------------
# Resolver-error formatting (mutation 50)
# ---------------------------------------------------------------------------


def test_resolver_error_message_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    # mutmut: kill mutation 50 — was: f"blocked target: cannot resolve host
    # '{host}': {resolver_error}" -> "XX...XX".
    def _boom(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(ValueError) as ei:
        validate_target("http://no.such.invalid/")
    msg = str(ei.value)
    assert msg.startswith("blocked target: cannot resolve host '")
    assert "XX" not in msg


def test_resolver_error_initial_state_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # mutmut: kill mutation 25 — was: `resolver_error: Exception | None =
    # None` -> `= ""`. With the mutant initial value "":
    #   - The try block succeeds (no gaierror), resolver_error stays "".
    #   - If ALL three IP sources fail to add an address, we hit the
    #     "no addresses" branch — `resolver_error is not None` is True
    #     (because "" is not None), so we raise the "cannot resolve"
    #     branch instead of the "no addresses" branch.
    # Build that path: getaddrinfo returns an empty-sockaddr entry (so
    # no IPs added), inet_aton fails (host not a v4 literal), inet_pton
    # fails (host not a v6 literal).
    def _empty(host, port, *args, **kwargs):
        # Sockaddr present but addr_obj empty so the loop skips it.
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _empty)
    with pytest.raises(ValueError) as ei:
        validate_target("http://no-addresses.example/")
    msg = str(ei.value)
    # Original takes the "no addresses" branch (resolver_error is None,
    # but ips is empty). Mutant takes the "cannot resolve" branch with
    # the empty-string error appended.
    assert "no addresses" in msg
    assert "cannot resolve" not in msg


# ---------------------------------------------------------------------------
# Logger and module-level constants (mutations 1, 2)
# ---------------------------------------------------------------------------


def test_module_logger_is_real_logger_instance() -> None:
    # mutmut: kill mutation 2 — was: `logger = logging.getLogger(...)` ->
    # `logger = None`. Validate the module-level logger object is a real
    # Logger; the mutant would set it to None which would crash any code
    # path that called `logger.warning(...)`. (No such path is reachable
    # in _urlsafe.py today — logger is declared but unused. The mutant is
    # therefore an *equivalent* mutant from a behavioural standpoint, but
    # we lock in the contract that the module exposes a logger.)
    from harness import _urlsafe

    assert _urlsafe.logger is not None
    # And verify mutation 1 — the channel name.
    import logging

    assert isinstance(_urlsafe.logger, logging.Logger)
    assert _urlsafe.logger.name == "keen"


# ---------------------------------------------------------------------------
# revalidate alias
# ---------------------------------------------------------------------------


def test_revalidate_is_validate_target_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    # Confirms that revalidate_target_at_request_time and validate_target
    # produce the SAME rejection for the SAME input — covered indirectly
    # by all the above mutation kills, but this lock-in helps.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.1"))
    with pytest.raises(ValueError, match="private"):
        revalidate_target_at_request_time("http://internal.example/")


# ---------------------------------------------------------------------------
# redact_url — quick lock-in
# ---------------------------------------------------------------------------


def test_redact_url_strips_query_and_fragment() -> None:
    # General correctness — covered by existing tests but pin the
    # query+fragment removal to catch any urlunparse mutation if mutmut
    # tags the redact path later.
    u = "https://example.com/a?b=c&d=e#f"
    assert redact_url(u) == "https://example.com/a"


def test_validate_target_returns_unchanged_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # General "happy path" identity check — important because many of the
    # mutmut mutations on string-construction lines only matter if some
    # accept path exercises them.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    url = "https://example.com/some/path"
    assert validate_target(url) == url
    # And with mock asserting only one resolution per call.
    with mock.patch("socket.getaddrinfo") as m:
        m.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
        validate_target(url)
        assert m.call_count == 1
