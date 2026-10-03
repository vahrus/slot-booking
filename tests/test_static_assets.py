"""Automatic content versions and the actual public/admin stylesheet URL."""

import os
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import static_assets
from tests.test_bookings import client, session_factory
from tests.test_public_ui import Document


def stylesheet_url(response) -> str:
    return Document(response.text).find("link", rel="stylesheet")["href"]


@pytest.mark.parametrize("path", ["/", "/admin", "/booking/999999"])
def test_template_stylesheet_has_automatic_version_and_static_request_succeeds(
    client: TestClient, path: str,
) -> None:
    html = client.get(path)
    href = stylesheet_url(html)
    parts = urlsplit(href)
    assert parts.path == "/static/style.css"
    version = parse_qs(parts.query)["v"][0]
    css = client.get(href)
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert version == sha256(css.content).hexdigest()[:16]
    assert stylesheet_url(client.get(path)) == href


def test_version_changes_with_css_bytes_even_if_file_mtime_is_unchanged(
    tmp_path, monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    css = tmp_path / "style.css"
    css.write_text("body { color: red; }")
    monkeypatch.setattr(static_assets, "STYLESHEET_PATH", css)
    original_stat = css.stat()
    original = stylesheet_url(client.get("/"))
    assert original == stylesheet_url(client.get("/admin"))
    css.write_text("body { color: tan; }")  # identical byte length and mtime
    os.utime(css, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    updated = stylesheet_url(client.get("/"))
    assert updated != original
    assert updated == stylesheet_url(client.get("/admin"))
    assert updated == stylesheet_url(client.get("/"))
    assert parse_qs(urlsplit(updated).query)["v"] == [sha256(css.read_bytes()).hexdigest()[:16]]


def test_url_for_preserves_https_host_and_proxy_root_path(client: TestClient) -> None:
    with TestClient(client.app, base_url="https://slotly.example", root_path="/appointments") as secure:
        href = stylesheet_url(secure.get("/"))
        parts = urlsplit(href)
        assert parts.scheme == "https"
        assert parts.netloc == "slotly.example"
        assert parts.path == "/appointments/static/style.css"
        assert parse_qs(parts.query)["v"] == [static_assets.stylesheet_version()]
        assert secure.get(href).status_code == 200
