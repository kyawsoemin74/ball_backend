import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqladmin.forms import get_model_form
from wtforms import FileField, SelectField

from app.admin import NewsAdmin
from app.db import async_session
from app.crud.news import create_news, get_news_by_category
from app.models.news import News, NewsCategory
from app.schemas.news import (
    News as NewsSchema,
    NewsCreate,
    NewsDetailResponse,
    NewsPagination,
    NewsResponse,
)


class AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.session = session
        self.added = []

    def add(self, instance):
        self.added.append(instance)
        self.session.add(instance)

    async def commit(self):
        self.session.commit()

    async def refresh(self, instance):
        self.session.refresh(instance)

    async def execute(self, statement):
        return self.session.execute(statement)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    News.__table__.create(engine)
    with Session(engine) as session:
        yield AsyncSessionAdapter(session)
    engine.dispose()


def test_model_default_is_canonical_latest(db_session):
    article = News(title="Default category", content="Test content")
    db_session.add(article)
    asyncio.run(db_session.commit())
    asyncio.run(db_session.refresh(article))

    assert article.category == "latest"
    assert type(News.__table__.c.category.default.arg) is str
    assert News.__table__.c.category.default.arg == "latest"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (NewsCategory.LATEST, "latest"),
        (NewsCategory.TRANSFERS, "transfers"),
        (NewsCategory.TIPS, "tips"),
        ("latest", "latest"),
        ("transfers", "transfers"),
        ("tips", "tips"),
    ],
)
def test_category_normalization_returns_canonical_values(value, expected):
    assert NewsCategory.normalize(value) == expected


@pytest.mark.parametrize("value", ["LATEST", "NewsCategory.LATEST", "invalid", None])
def test_category_normalization_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="Invalid news category"):
        NewsCategory.normalize(value)


def test_admin_category_field_has_only_canonical_choices():
    async def build_form():
        return await get_model_form(
            model=News,
            session_maker=async_session,
            exclude=NewsAdmin.form_excluded_columns,
            form_args=deepcopy(NewsAdmin.form_args),
            form_overrides=NewsAdmin.form_overrides,
        )

    form_class = asyncio.run(build_form())
    form = form_class()

    assert isinstance(form.category, SelectField)
    assert form.category.choices == [
        ("latest", "Latest"),
        ("transfers", "Transfers"),
        ("tips", "Tips"),
    ]
    assert isinstance(form.image_url, FileField)


def test_admin_category_field_rejects_invalid_submission():
    async def build_form():
        return await get_model_form(
            model=News,
            session_maker=async_session,
            exclude=NewsAdmin.form_excluded_columns,
            form_args=deepcopy(NewsAdmin.form_args),
            form_overrides=NewsAdmin.form_overrides,
        )

    form_class = asyncio.run(build_form())
    form = form_class(data={"category": "NewsCategory.LATEST"})

    assert form.category.validate(form) is False


def test_admin_hook_normalizes_category_and_preserves_image_upload(monkeypatch):
    from app import admin as admin_module

    class ImageFile:
        filename = "news.jpg"

    uploaded_url = "https://example.test/news.jpg"

    async def upload_image(file_obj):
        assert isinstance(file_obj, ImageFile)
        return uploaded_url

    monkeypatch.setattr(admin_module, "upload_news_image", upload_image)
    data = {"category": NewsCategory.LATEST, "image_url": ImageFile()}

    result = asyncio.run(
        NewsAdmin.on_model_change(None, data, None, True, None)
    )

    assert result["category"] == "latest"
    assert result["image_url"] == uploaded_url


def test_admin_hook_rejects_invalid_category_before_upload(monkeypatch):
    from app import admin as admin_module

    class ImageFile:
        filename = "news.jpg"

    async def unexpected_upload(_file_obj):
        pytest.fail("Invalid category must be rejected before image upload.")

    monkeypatch.setattr(admin_module, "upload_news_image", unexpected_upload)
    data = {"category": "NewsCategory.LATEST", "image_url": ImageFile()}

    with pytest.raises(ValueError, match="Invalid news category"):
        asyncio.run(NewsAdmin.on_model_change(None, data, None, True, None))


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (NewsCategory.LATEST, "latest"),
        ("transfers", "transfers"),
    ],
)
def test_create_news_persists_canonical_category(db_session, category, expected):
    article_data = NewsCreate(
        title="Category test",
        content="Test content",
        category=category,
    )

    article = asyncio.run(create_news(db_session, article_data))

    assert article.category == expected
    assert type(article.category) is str
    assert db_session.session.get(News, article.id).category == expected


def test_create_news_rejects_invalid_category_before_adding(db_session):
    article_data = NewsCreate.model_construct(
        title="Invalid category",
        content="Test content",
        category="NewsCategory.LATEST",
        image_url=None,
    )

    with pytest.raises(ValueError, match="Invalid news category"):
        asyncio.run(create_news(db_session, article_data))

    assert db_session.added == []


@pytest.mark.parametrize("category", ["latest", "transfers", "tips"])
def test_list_filter_and_response_serialization_accept_canonical_category(
    db_session, category
):
    article = News(
        title=f"{category} article",
        content="Test content",
        category=category,
    )
    db_session.add(article)
    asyncio.run(db_session.commit())
    asyncio.run(db_session.refresh(article))

    articles, total = asyncio.run(
        get_news_by_category(
            db_session,
            category,
            NewsPagination(limit=10, offset=0),
        )
    )
    response = NewsResponse(
        news=[NewsSchema.model_validate(item) for item in articles],
        total=total,
        limit=10,
        offset=0,
    )
    detail = NewsDetailResponse(
        data=NewsSchema.model_validate(articles[0])
    )

    assert total == 1
    assert [item.category.value for item in response.news] == [category]
    assert detail.model_dump(mode="json")["data"]["category"] == category
