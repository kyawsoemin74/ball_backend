import asyncio
from io import BytesIO

import pytest
from fastapi import UploadFile

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
