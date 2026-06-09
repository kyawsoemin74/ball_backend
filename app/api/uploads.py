from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.upload import NewsUploadResponse
from app.services.upload import upload_news_image

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/news", response_model=NewsUploadResponse)
async def upload_news_image_endpoint(file: UploadFile = File(...)):
    """Upload a news image to the shared uploads directory and return a public URL."""
    try:
        image_url = await upload_news_image(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Unable to save the uploaded image.") from exc

    return {"image_url": image_url}
