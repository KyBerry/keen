"""Tests for the SSRF guard in harness/_urlsafe.py."""

from __future__ import annotations

import socket
from unittest import mock

import pytest

from harness._urlsafe import (
    redact_url,
    revalidate_target_at_request_time,
    validate_target,
)

# --- Scheme rejection -----------------------------------------------------


def test_file_scheme_rejected_by_default() -> None:
    with pytest.raises(ValueError, match="file://"):
        validate_target("file:///etc/passwd")


def test_file_scheme_allowed_with_flag() -> None:
    # No DNS resolution needed for file://; should pass through unchanged.
    assert validate_target("file:///etc/passwd", allow_file=True) == "file:///etc/passwd"


def test_javascript_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported scheme"):
        validate_target("javascript:alert(1)")


def test_data_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported scheme"):
        validate_target("data:text/html,X")


def test_ftp_scheme_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported scheme"):
        validate_target("ftp://example.com/")


def test_empty_target_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        validate_target("")


def test_missing_hostname_rejected() -> None:
    with pytest.raises(ValueError, match="hostname"):
        validate_target("http:///path")


# --- Private / loopback / link-local IPs ----------------------------------


def _fake_getaddrinfo(ip: str):
    """Return a getaddrinfo replacement that always resolves to `ip`."""

    def _fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]

    return _fake


def test_aws_metadata_ip_rejected() -> None:
    with pytest.raises(ValueError, match=r"169\.254\.169\.254|link-local|blocked network"):
        validate_target("http://169.254.169.254/latest/meta-data/")


def test_localhost_rejected() -> None:
    # `localhost` resolves to 127.0.0.1 (or ::1) on every reasonable system.
    with pytest.raises(ValueError, match=r"loopback|localhost"):
        validate_target("http://localhost/")


def test_127_0_0_1_rejected() -> None:
    with pytest.raises(ValueError, match="loopback"):
        validate_target("http://127.0.0.1/")


def test_rfc1918_10_dot_rejected() -> None:
    with pytest.raises(ValueError, match="private"):
        validate_target("http://10.0.0.1/")


def test_rfc1918_192_168_rejected() -> None:
    with pytest.raises(ValueError, match="private"):
        validate_target("http://192.168.1.1/")


def test_rfc1918_172_16_rejected() -> None:
    with pytest.raises(ValueError, match="private"):
        validate_target("http://172.16.0.1/")


def test_cgnat_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # 100.64.0.0/10 isn't classified as private by ipaddress; we add it explicitly.
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("100.64.0.1"))
    with pytest.raises(ValueError, match=r"blocked network|100\.64"):
        validate_target("http://cgnat.example/")


def test_gcp_metadata_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("8.8.8.8"))
    with pytest.raises(ValueError, match="denylisted"):
        validate_target("http://metadata.google.internal/computeMetadata/v1/")


def test_resolver_failure_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(ValueError, match="cannot resolve"):
        validate_target("http://nope.invalid/")


# --- Public host accepted -------------------------------------------------


def test_public_host_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert validate_target("http://example.com/") == "http://example.com/"


def test_https_public_host_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert validate_target("https://example.com/path") == "https://example.com/path"


def test_allow_internal_skips_resolution() -> None:
    # allow_internal disables the IP-classification check entirely.
    # We don't monkeypatch — the call should not attempt resolution.
    with mock.patch("socket.getaddrinfo") as m:
        result = validate_target("http://localhost/", allow_internal=True)
        assert result == "http://localhost/"
        m.assert_not_called()


# --- redact_url -----------------------------------------------------------


def test_redact_url_strips_query_and_fragment() -> None:
    assert redact_url("https://x.com/a?token=secret#frag") == "https://x.com/a"


def test_redact_url_preserves_scheme_host_path() -> None:
    assert redact_url("https://example.com/a/b/c") == "https://example.com/a/b/c"


def test_redact_url_handles_empty_path() -> None:
    assert redact_url("https://example.com") == "https://example.com"


def test_redact_url_empty_input() -> None:
    assert redact_url("") == ""


def test_redact_url_non_string() -> None:
    # type: ignore -- intentionally passing non-string to verify defensive handling
    assert redact_url(None) == ""  # type: ignore[arg-type]


# --- revalidate_target_at_request_time -----------------------------------


def test_revalidate_alias_blocks_loopback() -> None:
    """The request-time alias must reject the same things as validate_target."""
    with pytest.raises(ValueError, match=r"loopback|169\.254|private"):
        revalidate_target_at_request_time("http://127.0.0.1/")


def test_revalidate_alias_blocks_aws_metadata() -> None:
    with pytest.raises(ValueError, match=r"169\.254|link-local|blocked network"):
        revalidate_target_at_request_time("http://169.254.169.254/latest/meta-data/")


def test_revalidate_alias_passes_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert revalidate_target_at_request_time("https://example.com/x") == "https://example.com/x"
