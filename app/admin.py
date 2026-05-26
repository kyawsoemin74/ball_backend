from sqladmin import Admin, ModelView
from app.models.standing import Standings
from app.models.match import Match
from app.models.news import News
from app.models.ad import Ad
from app.admin_auth import authentication_backend

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
    form_columns = ["title", "category", "content", "image_url"]
    # Form fields will be automatically generated from the model, 
    # but you can explicitly define them:
    
    name_plural = "News"

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