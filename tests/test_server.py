from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tamperlint.server.app import app

client = TestClient(app)


def test_health_and_rules() -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert any(r["id"] == "TL-REV-002" for r in client.get("/v1/rules").json())


def test_check_json_html_sarif(corpus: dict[str, bytes]) -> None:
    files = {"file": ("forged.pdf", corpus["overlay_edit"], "application/pdf")}
    body = client.post("/v1/check", files=files).json()
    assert body["verdict"] == "SUSPICIOUS"
    html = client.post("/v1/check?format=html", files=files)
    assert html.status_code == 200 and "text/html" in html.headers["content-type"]
    sarif = client.post("/v1/check?format=sarif", files=files).json()
    assert sarif["version"] == "2.1.0"


def test_rejects_bad_input() -> None:
    assert (
        client.post("/v1/check", files={"file": ("a.txt", b"hello", "text/plain")}).status_code
        == 415
    )
    assert (
        client.post("/v1/check", files={"file": ("a.pdf", b"", "application/pdf")}).status_code
        == 400
    )
    bad_type = client.post("/v1/check?doc_type=evil", files={"file": ("a.pdf", b"%PDF-", "x")})
    assert bad_type.status_code == 422


def test_html_response_has_security_headers(corpus: dict[str, bytes]) -> None:
    files = {"file": ("doc.pdf", corpus["genuine"], "application/pdf")}
    response = client.post("/v1/check?format=html", files=files)
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_errors_do_not_leak_internals() -> None:
    files = {"file": ("broken.pdf", b"%PDF-1.7 not really a pdf", "application/pdf")}
    response = client.post("/v1/check", files=files)
    assert response.status_code == 415
    assert "BytesIO" not in response.text and "0x" not in response.text


def test_oversized_upload_is_rejected_early(monkeypatch: pytest.MonkeyPatch) -> None:
    import tamperlint.server.app as server

    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1024)
    files = {"file": ("big.pdf", b"%PDF-" + b"0" * 200_000, "application/pdf")}
    assert client.post("/v1/check", files=files).status_code == 413
