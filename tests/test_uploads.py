import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from app.api.deps import current_active_admin
from app.api import uploads as uploads_api
import app.services.upload as upload_service
from app.services.upload import upload_news_image


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("bad.txt", b"not-an-image"),
        ("bad.exe", b"\x89PNG\r\n\x1a\nnot-really-png"),
    ],
)
def test_upload_news_image_rejects_non_image_files(filename, content):
    file = UploadFile(filename=filename, file=BytesIO(content))

    with pytest.raises(ValueError):
        asyncio.run(upload_news_image(file, upload_dir="/tmp/fover-test-uploads", public_base_url="https://example.test/uploads/news/"))


def test_upload_news_image_saves_file_and_returns_public_url(tmp_path, monkeypatch):
    target_dir = tmp_path / "news"
    public_base_url = "https://example.test/uploads/news/"

    file = UploadFile(filename="hero.png", file=BytesIO(MINI_PNG))

    url = asyncio.run(upload_news_image(file, upload_dir=str(target_dir), public_base_url=public_base_url))

    assert url.startswith(public_base_url)
    assert (target_dir / url.split("/")[-1]).exists()
    assert (target_dir / url.split("/")[-1]).stat().st_size > 0


def test_upload_news_image_uses_configured_storage_settings(tmp_path, monkeypatch):
    target_dir = tmp_path / "configured-news"
    public_base_url = "http://localhost:8081/uploads/news/"
    monkeypatch.setattr(
        upload_service,
        "settings",
        SimpleNamespace(
            NEWS_UPLOAD_DIR=str(target_dir),
            NEWS_UPLOAD_PUBLIC_URL=public_base_url,
        ),
    )
    file = UploadFile(filename="configured.png", file=BytesIO(MINI_PNG))

    url = asyncio.run(upload_news_image(file))

    filename = url.rsplit("/", 1)[-1]
    assert url.startswith(public_base_url)
    assert (target_dir / filename).read_bytes() == MINI_PNG


def test_upload_endpoint_response_shape_is_unchanged(monkeypatch):
    async def upload_image(_file):
        return "http://localhost:8081/uploads/news/example.png"

    monkeypatch.setattr(uploads_api, "upload_news_image", upload_image)
    test_app = FastAPI()
    test_app.include_router(uploads_api.router, prefix="/api")
    test_app.dependency_overrides[current_active_admin] = lambda: object()

    with TestClient(test_app) as client:
        response = client.post(
            "/api/uploads/news",
            files={"file": ("example.png", MINI_PNG, "image/png")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "image_url": "http://localhost:8081/uploads/news/example.png"
    }
