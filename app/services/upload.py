import asyncio
import inspect
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024


async def _read_file_bytes(file_obj: Any) -> bytes:
    """Read bytes from a FastAPI UploadFile or a WTForms/FileStorage-like object."""
    read_fn = getattr(file_obj, "read", None)
    if not callable(read_fn):
        raise ValueError("Invalid file upload object.")

    try:
        content = read_fn()
        if inspect.isawaitable(content):
            content = await content
    except Exception as exc:
        raise ValueError("Unable to read the uploaded file.") from exc

    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("The uploaded file is invalid.")

    return bytes(content)


async def validate_image_upload(file_obj: Any) -> bytes:
    """Validate that the incoming file is an image and within the size limit."""
    if file_obj is None:
        raise ValueError("No file was provided.")

    filename = getattr(file_obj, "filename", None) or getattr(file_obj, "name", "")
    if not filename:
        raise ValueError("The uploaded file is missing a filename.")

    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Only JPG, JPEG, PNG, and WEBP image files are allowed.")

    content = await _read_file_bytes(file_obj)
    if len(content) == 0:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise ValueError("The uploaded file exceeds the 5 MB limit.")

    detected_format = _detect_image_format(content)
    if detected_format not in {"jpeg", "png", "webp"}:
        raise ValueError("The uploaded file is not a valid image.")

    return content


def _detect_image_format(content: bytes) -> str | None:
    """Detect a supported image format using file signatures."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
        return "webp"
    return None


async def upload_news_image(
    file_obj: Any,
    upload_dir: Optional[str] = None,
    public_base_url: Optional[str] = None,
) -> str:
    """Validate, save, and return a public URL for a news image upload."""
    content = await validate_image_upload(file_obj)

    target_dir = Path(upload_dir or getattr(settings, "NEWS_UPLOAD_DIR", "/var/www/fover/uploads/news"))
    base_url = public_base_url or getattr(settings, "NEWS_UPLOAD_PUBLIC_URL", "https://kyawsoemin.com/uploads/news/")

    target_dir.mkdir(parents=True, exist_ok=True)

    filename = getattr(file_obj, "filename", None) or getattr(file_obj, "name", "")
    extension = Path(filename).suffix.lower() if filename else ".jpg"
    unique_name = f"{uuid.uuid4().hex}{extension}"
    destination = target_dir / unique_name

    try:
        await asyncio.to_thread(destination.write_bytes, content)
    except OSError as exc:
        raise OSError("Unable to save the uploaded image to the storage directory.") from exc

    return f"{base_url.rstrip('/')}/{unique_name}"
