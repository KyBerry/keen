"""Tests for the SSRF guard in harness/_urlsafe.py."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from harness._urlsafe import (
    file_document_path,
    file_scope_root,
    network_origin,
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


@pytest.mark.parametrize(
    "target",
    [
        "file://attacker.example/share/index.html",
        "file://127.0.0.1/share",
        "file://localhost/share",
        "file://C:/windows",
        "file:////attacker/share",
        "file://///attacker/share",
        r"file:\\attacker\share",
        r"file:///\\attacker\share",
        "file:///%2F%2Fattacker/share",
        "file:///%5C%5Cattacker/share",
        "file:///%255C%255Cattacker/share",
        "file:////%61ttacker/share",
        "file:C:/relative",
        "file:",
        " file:///etc/passwd",
    ],
)
def test_allow_file_rejects_remote_or_ambiguous_file_urls(target: str) -> None:
    with pytest.raises(ValueError, match="blocked target"):
        validate_target(target, allow_file=True)
    with pytest.raises(ValueError, match="blocked target"):
        revalidate_target_at_request_time(target, allow_file=True)


@pytest.mark.parametrize(
    "target",
    [
        "file:/tmp/local.html",
        "file:///tmp/design%20review.html",
        "file:/C:/Users/example/review.html",
        "file:///C:/Users/example/review.html",
    ],
)
def test_allow_file_accepts_authority_free_absolute_local_urls(target: str) -> None:
    assert validate_target(target, allow_file=True) == target


def test_file_request_revalidation_requires_an_explicit_root(tmp_path) -> None:
    target = (tmp_path / "site" / "index.html").as_uri()
    with pytest.raises(ValueError, match="allowed local roots"):
        revalidate_target_at_request_time(target, allow_file=True)


def test_file_request_scope_allows_sibling_assets_but_blocks_other_roots(tmp_path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    index = site / "index.html"
    asset = site / "assets" / "app.css"
    asset.parent.mkdir()
    outside = tmp_path / "private.txt"
    index.write_text("<link rel='stylesheet' href='assets/app.css'>", encoding="utf-8")
    asset.write_text("body {}", encoding="utf-8")
    outside.write_text("secret", encoding="utf-8")

    roots = (file_scope_root(index.as_uri()),)
    assert (
        revalidate_target_at_request_time(
            asset.as_uri(),
            allow_file=True,
            file_roots=roots,
            resource_type="stylesheet",
        )
        == asset.as_uri()
    )
    with pytest.raises(ValueError, match="outside the allowed local roots"):
        revalidate_target_at_request_time(
            outside.as_uri(),
            allow_file=True,
            file_roots=roots,
            resource_type="stylesheet",
        )


def test_file_request_scope_resolves_traversal_and_symlink_escapes(tmp_path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    index = site / "index.html"
    index.write_text("local", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("secret", encoding="utf-8")
    link = site / "linked.txt"
    link.symlink_to(outside)
    roots = (file_scope_root(index.as_uri()),)

    escaped = f"{site.as_uri()}/%2e%2e/private.txt"
    for target in (escaped, link.as_uri()):
        with pytest.raises(ValueError, match="outside the allowed local roots"):
            revalidate_target_at_request_time(
                target,
                allow_file=True,
                file_roots=roots,
                resource_type="stylesheet",
            )


def test_file_directory_target_grants_itself_not_parent(tmp_path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    root = file_scope_root(f"{site.as_uri()}/")
    assert root == site.resolve()


def test_file_documents_require_an_exact_explicit_path(tmp_path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    index = site / "index.html"
    sibling = site / ".env"
    index.write_text("surface", encoding="utf-8")
    sibling.write_text("TOKEN=secret", encoding="utf-8")
    roots = (file_scope_root(index.as_uri()),)
    documents = (file_document_path(index.as_uri()),)

    assert (
        revalidate_target_at_request_time(
            index.as_uri(),
            allow_file=True,
            file_roots=roots,
            file_documents=documents,
            resource_type="document",
            is_navigation_request=True,
        )
        == index.as_uri()
    )
    with pytest.raises(ValueError, match="document was not explicitly requested"):
        revalidate_target_at_request_time(
            sibling.as_uri(),
            allow_file=True,
            file_roots=roots,
            file_documents=documents,
            resource_type="document",
            is_navigation_request=True,
        )
    with pytest.raises(ValueError, match="publication asset"):
        revalidate_target_at_request_time(
            sibling.as_uri(),
            allow_file=True,
            file_roots=roots,
            file_documents=documents,
            resource_type="script",
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows file-URL drive conversion")
def test_windows_file_scope_uses_the_url_drive_path() -> None:
    target = "file:///C:/Users/example/site/index.html"
    expected_root = Path(r"C:\Users\example\site").resolve(strict=False)

    root = file_scope_root(target)

    assert root == expected_root
    assert (
        revalidate_target_at_request_time(
            "file:///C:/Users/example/site/assets/app.css",
            allow_file=True,
            file_roots=(root,),
            resource_type="stylesheet",
        )
        == "file:///C:/Users/example/site/assets/app.css"
    )
    with pytest.raises(ValueError, match="outside the allowed local roots"):
        revalidate_target_at_request_time(
            "file:///C:/Users/example/private.txt",
            allow_file=True,
            file_roots=(root,),
            resource_type="stylesheet",
        )


def test_allow_file_rejects_percent_encoded_control_character() -> None:
    with pytest.raises(ValueError, match="control characters"):
        validate_target("file:///tmp/review%00.html", allow_file=True)


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


def test_allow_internal_permits_resolved_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    assert validate_target("http://localhost/", allow_internal=True) == "http://localhost/"


def test_request_time_internal_access_is_scoped_to_exact_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    allowed = (network_origin("http://127.0.0.1:3000"),)

    assert (
        revalidate_target_at_request_time(
            "http://127.0.0.1:3000/app.js",
            allow_internal=True,
            internal_origins=allowed,
        )
        == "http://127.0.0.1:3000/app.js"
    )
    with pytest.raises(ValueError, match="loopback"):
        revalidate_target_at_request_time(
            "http://127.0.0.1:8080/admin",
            allow_internal=True,
            internal_origins=allowed,
        )


def test_allow_origin_must_be_an_origin_without_a_path() -> None:
    assert network_origin(
        "http://localhost:8787/",
        require_origin_only=True,
    ) == ("http", "localhost", 8787)
    with pytest.raises(ValueError, match="must not include a path"):
        network_origin(
            "http://localhost:8787/api",
            require_origin_only=True,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/v2/credentials/",
        "http://169.254.170.23/v1/credentials/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://100.64.0.1/",
    ],
)
def test_allow_internal_never_permits_explicit_deny_networks(url: str) -> None:
    with pytest.raises(ValueError, match="blocked network"):
        validate_target(url, allow_internal=True)


# --- redact_url -----------------------------------------------------------


def test_redact_url_strips_query_and_fragment() -> None:
    assert redact_url("https://x.com/a?token=secret#frag") == "https://x.com/a"


def test_redact_url_preserves_scheme_host_path() -> None:
    assert redact_url("https://example.com/a/b/c") == "https://example.com/a/b/c"


def test_redact_url_strips_userinfo_and_preserves_port() -> None:
    assert (
        redact_url("https://user:password@example.com:8443/a?token=secret")
        == "https://example.com:8443/a"
    )


def test_validate_target_rejects_url_userinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    with pytest.raises(ValueError, match="userinfo"):
        validate_target("https://user:password@example.com/a")


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com:99999/",
        "https://example.com/\nadmin",
    ],
)
def test_validate_target_rejects_malformed_absolute_url(target: str) -> None:
    with pytest.raises(ValueError, match="blocked target"):
        validate_target(target)


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
