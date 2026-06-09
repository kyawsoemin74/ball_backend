from pydantic import BaseModel


class NewsUploadResponse(BaseModel):
    image_url: str
