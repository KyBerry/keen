"""Browser-level capture-integrity regressions against a local HTTP server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from harness import _artifacts, capture, cli


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/poc"):
            self.send_response(302)
            self.send_header("Location", "/sign-in?callback_token=server-secret")
            self.end_headers()
            return
        if self.path.startswith("/canonical"):
            self.send_response(302)
            self.send_header("Location", "/ready/")
            self.end_headers()
            return
        websocket_script = (
            b"<script>new WebSocket('ws://' + location.host + '/socket')</script>"
            if self.path.startswith("/websocket")
            else b""
        )
        if self.path.startswith("/empty"):
            body = b"<!doctype html><html><head><title>Empty</title></head><body></body></html>"
        elif self.path.startswith("/body-text"):
            body = (
                b"<!doctype html><html><head><title>Text</title></head>"
                b"<body>Hello world</body></html>"
            )
        elif self.path.startswith("/multiple-ready"):
            body = (
                b"<!doctype html><html><head><title>Multiple</title></head><body>"
                b'<main class="ready">One</main><aside class="ready">Two</aside>'
                b"</body></html>"
            )
        elif self.path.startswith("/href-secrets"):
            body = (
                b"<!doctype html><html><head><title>Links</title></head><body>"
                b'<main data-review-ready="true"><a href="'
                b"https://alice:HREF_USERINFO_SENTINEL@example.com/account"
                b'?access_token=HREF_QUERY_SENTINEL#private">Account</a></main>'
                b"</body></html>"
            )
        else:
            body = (
                b"<!doctype html><html><head><title>Ready</title></head>"
                b'<body><main data-review-ready="true">Review surface</main>'
                + websocket_script
                + b"</body></html>"
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def local_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_auth_redirect_is_diagnostic_only_and_never_scored(
    tmp_path: Path,
    local_server: str,
) -> None:
    out = tmp_path / "blocked"

    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "capture",
                f"{local_server}/poc?preview_token=client-secret",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--out",
                str(out),
            ]
        )

    assert exc.value.code == 1
    manifest = json.loads((out / "capture-manifest.json").read_text())
    assert manifest["status"] == "blocked"
    assert manifest["reviewable"] is False
    assert manifest["failures"][0]["reason_code"] == "unexpected-url"
    assert manifest["failures"][0]["diagnostic_only"] is True
    brief = json.loads((out / "agent-brief.json").read_text())
    assert brief["review_status"]["status"] == "blocked"
    assert brief["grade"] is None
    assert not (out / "report.json").exists()
    assert not (out / "analysis").exists()
    artifact_text = "\n".join(
        path.read_text(errors="ignore")
        for path in out.rglob("*")
        if path.is_file() and path.suffix in {".json", ".md"}
    )
    assert "client-secret" not in artifact_text
    assert "server-secret" not in artifact_text


def test_internal_permission_does_not_grant_other_loopback_origins(
    tmp_path: Path,
) -> None:
    admin_requests: list[str] = []

    class AdminHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            admin_requests.append(self.path)
            body = b"<html><body>PRIVATE ADMIN</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    admin = ThreadingHTTPServer(("127.0.0.1", 0), AdminHandler)
    admin_thread = threading.Thread(target=admin.serve_forever, daemon=True)
    admin_thread.start()
    admin_host, admin_port = admin.server_address

    class SurfaceHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = (
                b"<!doctype html><html><body><main>Reviewed app</main>"
                + (f'<iframe src="http://{admin_host}:{admin_port}/admin"></iframe>').encode()
                + b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    surface = ThreadingHTTPServer(("127.0.0.1", 0), SurfaceHandler)
    surface_thread = threading.Thread(target=surface.serve_forever, daemon=True)
    surface_thread.start()
    surface_host, surface_port = surface.server_address
    try:
        out = tmp_path / "scoped-internal"
        assert (
            cli.main(
                [
                    "capture",
                    f"http://{surface_host}:{surface_port}/",
                    "--allow-internal",
                    "--viewports",
                    "desktop",
                    "--no-full-page",
                    "--out",
                    str(out),
                ]
            )
            == 0
        )
        assert admin_requests == []
        manifest = json.loads((out / "capture-manifest.json").read_text())
        assert manifest["status"] == "complete"

        allowed_out = tmp_path / "explicit-internal"
        assert (
            cli.main(
                [
                    "capture",
                    f"http://{surface_host}:{surface_port}/",
                    "--allow-internal",
                    "--allow-origin",
                    f"http://{admin_host}:{admin_port}",
                    "--viewports",
                    "desktop",
                    "--no-full-page",
                    "--out",
                    str(allowed_out),
                ]
            )
            == 0
        )
        assert admin_requests == ["/admin"]
    finally:
        surface.shutdown()
        surface.server_close()
        surface_thread.join(timeout=2)
        admin.shutdown()
        admin.server_close()
        admin_thread.join(timeout=2)


def test_file_capture_blocks_unlisted_sibling_documents(
    tmp_path: Path,
) -> None:
    site = tmp_path / "local-site"
    site.mkdir()
    target = site / "index.html"
    secret = site / "secret.html"
    target.write_text(
        (
            "<!doctype html><html><body><main>Reviewed local app</main>"
            '<iframe src="secret.html" width="400" height="300" '
            'style="border:0"></iframe></body></html>'
        ),
        encoding="utf-8",
    )
    secret.write_text(
        (
            "<!doctype html><html><body style='margin:0;background:#ff0000;"
            "width:100vw;height:100vh'>PRIVATE LOCAL FILE</body></html>"
        ),
        encoding="utf-8",
    )
    out = tmp_path / "scoped-file"

    assert (
        cli.main(
            [
                "capture",
                target.as_uri(),
                "--allow-file",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--out",
                str(out),
            ]
        )
        == 0
    )

    manifest = json.loads((out / "capture-manifest.json").read_text())
    assert manifest["status"] == "complete"
    screenshot = out / manifest["succeeded"][0]["screen"]
    with Image.open(screenshot) as image:
        red_pixels = sum(
            1
            for red, green, blue in image.convert("RGB").get_flattened_data()
            if red > 240 and green < 15 and blue < 15
        )
    assert red_pixels == 0


def test_interrupted_manifest_commit_leaves_run_unreviewable(
    tmp_path: Path,
    local_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "interrupted"
    real_atomic_write = capture.safeio_mod.atomic_write_text

    def interrupt_manifest(root: Path, path: Path, text: str, **kwargs) -> Path:
        if Path(path).name == "capture-manifest.json":
            raise OSError("simulated interruption before manifest commit")
        return real_atomic_write(root, path, text, **kwargs)

    monkeypatch.setattr(capture.safeio_mod, "atomic_write_text", interrupt_manifest)

    assert (
        cli.main(
            [
                "capture",
                f"{local_server}/ready",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--out",
                str(out),
            ]
        )
        == 1
    )
    assert (out / _artifacts.CAPTURE_IN_PROGRESS_FILENAME).is_file()
    assert not (out / "capture-manifest.json").exists()
    assert list((out / "dom").glob("*.json"))

    with pytest.raises(SystemExit) as exc:
        cli.main(["audit", str(out)])
    assert exc.value.code == 1
    assert not (out / "report.json").exists()


def test_href_credentials_never_reach_pipeline_artifacts(
    tmp_path: Path,
    local_server: str,
) -> None:
    out = tmp_path / "href-sanitized"
    assert (
        cli.main(
            [
                "review",
                f"{local_server}/href-secrets",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--expect-selector",
                "[data-review-ready]",
                "--out",
                str(out),
            ]
        )
        == 0
    )

    artifact_text = "\n".join(
        path.read_text(errors="ignore")
        for path in out.rglob("*")
        if path.is_file() and path.suffix in {".html", ".json", ".md"}
    )
    assert "HREF_USERINFO_SENTINEL" not in artifact_text
    assert "HREF_QUERY_SENTINEL" not in artifact_text
    assert "https://example.com/account" in artifact_text


def test_explicit_expected_redirect_and_selector_are_reviewable(
    tmp_path: Path,
    local_server: str,
) -> None:
    out = tmp_path / "ready"

    result = cli.main(
        [
            "capture",
            f"{local_server}/canonical",
            "--allow-internal",
            "--viewports",
            "desktop",
            "--no-full-page",
            "--expect-url",
            f"{local_server}/ready/",
            "--expect-selector",
            "[data-review-ready]",
            "--out",
            str(out),
        ]
    )

    assert result == 0
    manifest = json.loads((out / "capture-manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["reviewable"] is True
    dom_path = out / manifest["succeeded"][0]["dom"]
    dom = json.loads(dom_path.read_text())
    assert dom["coverage"]["expectation"]["status"] == "matched"
    assert dom["url"] == f"{local_server}/ready/"


def test_auth_bootstrap_failure_never_exposes_navigation_secret(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "auth-failure"
    auth_steps = tmp_path / "auth-steps.json"
    auth_steps.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "goto",
                        "url": "http://127.0.0.1:1/login?token=AUTH_SETUP_SENTINEL",
                        "timeout_ms": 100,
                    }
                ]
            }
        )
    )

    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "capture",
                "http://127.0.0.1:1/product",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--auth-steps",
                str(auth_steps),
                "--out",
                str(out),
            ]
        )

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "AUTH_SETUP_SENTINEL" not in stderr
    assert "browser authentication failed" in stderr


def test_websocket_dependent_surface_is_marked_provisional(
    tmp_path: Path,
    local_server: str,
) -> None:
    out = tmp_path / "websocket"
    assert (
        cli.main(
            [
                "capture",
                f"{local_server}/websocket",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    manifest = json.loads((out / "capture-manifest.json").read_text())
    dom = json.loads((out / manifest["succeeded"][0]["dom"]).read_text())
    assert dom["coverage"]["complete"] is False
    assert dom["coverage"]["reason"] == "websockets-blocked"
    assert dom["meta"]["manual_review_needed"] is True
    assert dom["meta"]["websockets_blocked"]["attempted"] >= 1


def test_empty_surface_is_unscored_and_requires_manual_review(
    tmp_path: Path,
    local_server: str,
) -> None:
    out = tmp_path / "empty"
    assert (
        cli.main(
            [
                "capture",
                f"{local_server}/empty",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--out",
                str(out),
            ]
        )
        == 0
    )

    manifest = json.loads((out / "capture-manifest.json").read_text())
    assert manifest["status"] == "partial"
    assert manifest["complete"] is False
    dom = json.loads((out / manifest["succeeded"][0]["dom"]).read_text())
    assert dom["coverage"]["reason"] == "empty-surface"
    assert dom["meta"]["empty_surface"] is True
    assert dom["meta"]["manual_review_needed"] is True

    assert cli.main(["audit", str(out)]) == 0
    report = json.loads((out / "report.json").read_text())
    assert report["score"]["grade"] == "INCOMPLETE"
    assert report["score"]["score"] is None
    assert report["coverage"]["reason"] == "empty-surface"
    assert report["review_status"]["status"] == "incomplete"
    assert report["review_status"]["reviewable"] is False


def test_direct_body_text_is_rendered_surface_not_empty_shell(
    tmp_path: Path,
    local_server: str,
) -> None:
    out = tmp_path / "body-text"
    assert (
        cli.main(
            [
                "capture",
                f"{local_server}/body-text",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--out",
                str(out),
            ]
        )
        == 0
    )

    manifest = json.loads((out / "capture-manifest.json").read_text())
    assert manifest["status"] == "complete"
    dom = json.loads((out / manifest["succeeded"][0]["dom"]).read_text())
    assert dom["surface"]["body_text_chars"] == len("Hello world")
    assert dom["coverage"]["reason"] is None
    assert dom["meta"]["empty_surface"] is False


def test_expected_selector_accepts_multiple_visible_matches(
    tmp_path: Path,
    local_server: str,
) -> None:
    out = tmp_path / "multiple-ready"
    assert (
        cli.main(
            [
                "capture",
                f"{local_server}/multiple-ready",
                "--allow-internal",
                "--viewports",
                "desktop",
                "--no-full-page",
                "--expect-selector",
                ".ready",
                "--out",
                str(out),
            ]
        )
        == 0
    )

    manifest = json.loads((out / "capture-manifest.json").read_text())
    assert manifest["status"] == "complete"
    dom = json.loads((out / manifest["succeeded"][0]["dom"]).read_text())
    assert dom["coverage"]["expectation"]["status"] == "matched"
