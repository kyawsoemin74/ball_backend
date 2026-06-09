from sqladmin import Admin, ModelView
from wtforms import FileField

from app.models.standing import Standings
from app.models.match import Match
from app.models.news import News
from app.models.ad import Ad
from app.admin_auth import authentication_backend
from app.services.upload import upload_news_image

class StandingsAdmin(ModelView, model=Standings):
    column_list = ["id", "league_id", "rank", "team_name", "points"] # String ပုံစံပြောင်းခြင်း
    column_searchable_list = ["team_name"]
    name_plural = "Standings"

class MatchAdmin(ModelView, model=Match):
    column_list = ["id", "home_team", "away_team", "status", "match_time"] # String ပုံစံပြောင်းခြင်း
    column_labels = {"match_time": "Date"}
    name_plural = "Matches"

class NewsAdmin(ModelView, model=News):
    column_list = ["id", "title", "category", "image_url", "created_at"]
    form_excluded_columns = [
        "published_at",
        "created_at",
        "updated_at",
    ]
    # SQLAdmin 0.25.0 exposes form_overrides in ModelView and uses it when
    # building forms via get_model_form(..., form_overrides=...).
    form_overrides = {"image_url": FileField}
    form_args = {"image_url": {"label": "Upload News Image"}}
    name_plural = "News"

    async def on_model_change(self, data: dict, model: News, is_created: bool, request) -> dict:
        """Handle optional image uploads for News records.

        Create path: when a file is attached, upload it and store the public URL.
        Edit path: keep the existing image_url unless a real replacement file is provided.
        """
        image_file = data.get("image_url")

        # Create path: if no file is attached, the field stays empty.
        # Edit path: if the field was left untouched, preserve the current image_url.
        if not getattr(image_file, "filename", None):
            if not is_created and getattr(model, "image_url", None):
                data["image_url"] = model.image_url
            return data

        try:
            image_url = await upload_news_image(image_file)
        except (ValueError, OSError) as exc:
            raise ValueError(f"Image upload failed: {exc}") from exc

        # Only replace image_url after a successful upload.
        data["image_url"] = image_url
        return data

class AdsAdmin(ModelView, model=Ad):
    column_list = ["id", "title", "is_active"]
    name_plural = "Advertisements"

def setup_admin(app, engine):
    """
    Initialize SQLAdmin with the FastAPI app and SQLAlchemy engine.
    """
    admin = Admin(
        app=app, 
        engine=engine, 
        authentication_backend=authentication_backend,
        base_url="/admin"
    )
    for view in [StandingsAdmin, MatchAdmin, NewsAdmin, AdsAdmin]:
        admin.add_view(view)