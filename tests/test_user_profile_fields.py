from app.models.user import AvatarSource, User


def test_user_profile_fields_exist_on_model():
    column_names = {column.name for column in User.__table__.columns}

    assert "display_name" in column_names
    assert "avatar_url" in column_names
    assert "avatar_source" in column_names


def test_avatar_source_enum_uses_database_values():
    enum_type = User.__table__.c.avatar_source.type

    assert enum_type.enums == [AvatarSource.DEFAULT.value, AvatarSource.GOOGLE.value, AvatarSource.UPLOAD.value]
