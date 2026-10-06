import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, UniqueConstraint, func, text
from sqlalchemy.orm import relationship

from app.db import Base


class AvatarSource(str, enum.Enum):
    DEFAULT = "default"
    GOOGLE = "google"
    UPLOAD = "upload"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    id = Column(Integer, primary_key=True)
    username = Column(String(50), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    google_id = Column(String(255), unique=True, nullable=True)
    role = Column(String(20), nullable=False, server_default="user")
    is_active = Column(Boolean(), nullable=False, server_default=text("true"))
    display_name = Column(String(100), nullable=True)
    avatar_url = Column(String(2048), nullable=True)
    avatar_source = Column(
        Enum(
            AvatarSource,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=AvatarSource.DEFAULT.value,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    auth_sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")
