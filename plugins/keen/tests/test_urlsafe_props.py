"""Property-based tests for harness/_urlsafe.py.

Hypothesis docs for the strategies used here:
- ip_addresses: https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.ip_addresses
- text: https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.text
"""

from __future__ import annotations

import ipaddress
import socket
import string
from urllib.parse import urlparse

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from harness._urlsafe import redact_url, validate_target


def _fake_getaddrinfo_for(ip_str: str):
    def _fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip_str, 0))]

    return _fake


# --- public IPs are accepted ---------------------------------------------

# Sample of public-IPv4 starts (NOT exhaustive — only ranges that are firmly
# public and not in any RFC1918 / loopback / link-local / multicast / reserved
# / explicit denylist). 8.x and 216.x are routinely used as canonical
# "public" addresses (Google DNS, example.com historically).
_PUBLIC_IPV4_BLOCKS = [
    ipaddress.IPv4Network("8.0.0.0/8"),
    ipaddress.IPv4Network("9.0.0.0/8"),
    ipaddress.IPv4Network("216.0.0.0/8"),
]


@st.composite
def _public_ipv4(draw):
    block = draw(st.sampled_from(_PUBLIC_IPV4_BLOCKS))
    # Pick an offset inside the /8, avoiding network/broadcast just to be safe.
    offset = draw(st.integers(min_value=1, max_value=block.num_addresses - 2))
    addr = block.network_address + offset
    return str(addr)


@given(ip=_public_ipv4())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_public_ip_accepted(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    """Public IPv4 addresses should pass validate_target."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_for(ip))
    url = f"http://{ip}/"
    assert validate_target(url, allow_internal=False) == url


# --- private / loopback / link-local IPs are rejected --------------------


@st.composite
def _private_ipv4(draw):
    """Generate an IPv4 known to be in a blocked range.

    Includes RFC1918, loopback, link-local, and the AWS metadata IP.
    """
    kind = draw(st.sampled_from(["10", "172", "192", "127", "169", "100"]))
    if kind == "10":
        octets = [
            10,
            draw(st.integers(0, 255)),
            draw(st.integers(0, 255)),
            draw(st.integers(1, 254)),
        ]
    elif kind == "172":
        octets = [
            172,
            draw(st.integers(16, 31)),
            draw(st.integers(0, 255)),
            draw(st.integers(1, 254)),
        ]
    elif kind == "192":
        octets = [192, 168, draw(st.integers(0, 255)), draw(st.integers(1, 254))]
    elif kind == "127":  # loopback
        octets = [
            127,
            draw(st.integers(0, 255)),
            draw(st.integers(0, 255)),
            draw(st.integers(1, 254)),
        ]
    elif kind == "169":  # link-local
        octets = [169, 254, draw(st.integers(0, 255)), draw(st.integers(1, 254))]
    else:  # CGNAT 100.64.0.0/10
        octets = [
            100,
            draw(st.integers(64, 127)),
            draw(st.integers(0, 255)),
            draw(st.integers(1, 254)),
        ]
    return ".".join(str(o) for o in octets)


@given(ip=_private_ipv4())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_private_ip_rejected(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    """Every IP in a private/loopback/link-local/CGNAT range must be blocked."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_for(ip))
    with pytest.raises(ValueError, match="blocked target"):
        validate_target(f"http://{ip}/")


# --- arbitrary strings: no crash, must accept or raise ValueError --------


@given(
    s=st.text(
        alphabet=string.printable,
        min_size=0,
        max_size=80,
    ),
)
@settings(max_examples=200)
def test_validate_target_never_crashes_on_text(s: str) -> None:
    """validate_target must either return a string or raise ValueError —
    never an unhandled exception like AttributeError or TypeError."""
    try:
        result = validate_target(s, allow_internal=True)
        assert isinstance(result, str)
    except ValueError:
        pass  # expected for malformed input


# --- redact_url properties -----------------------------------------------


@given(url=st.text(alphabet=string.printable, min_size=0, max_size=200))
@settings(max_examples=200)
def test_redact_url_strips_query_and_fragment(url: str) -> None:
    """Output of redact_url contains no '?' or '#' characters."""
    out = redact_url(url)
    assert isinstance(out, str)
    assert "?" not in out
    assert "#" not in out


@given(url=st.text(alphabet=string.printable, min_size=0, max_size=200))
@settings(max_examples=200)
def test_redact_url_idempotent(url: str) -> None:
    """redact_url(redact_url(u)) == redact_url(u)."""
    once = redact_url(url)
    twice = redact_url(once)
    assert once == twice


# Domain alphabet (kept simple — Hypothesis text strategy doc:
# https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.text)
_DOMAIN_LABEL = st.text(
    alphabet=st.sampled_from(string.ascii_lowercase + string.digits),
    min_size=1,
    max_size=12,
)


@st.composite
def _valid_http_url(draw):
    scheme = draw(st.sampled_from(["http", "https"]))
    parts = draw(st.lists(_DOMAIN_LABEL, min_size=2, max_size=4))
    host = ".".join(parts)
    path = draw(st.lists(_DOMAIN_LABEL, min_size=0, max_size=3))
    return f"{scheme}://{host}/" + "/".join(path)


@given(url=_valid_http_url())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_redact_preserves_scheme_and_netloc_for_accepted_urls(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """For any URL validate_target ACCEPTS, redact_url preserves scheme and netloc."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_for("8.8.8.8"))
    try:
        validate_target(url)
    except ValueError:
        assume(False)
    redacted = redact_url(url)
    p_orig = urlparse(url)
    p_red = urlparse(redacted)
    assert p_orig.scheme == p_red.scheme
    assert p_orig.netloc == p_red.netloc
