"""Test-only model for exercising BaseRepository independently of Widget."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base


class DummyModel(Base):
    """Minimal model for testing BaseRepository generic behavior."""

    __tablename__ = "dummy_model"

    name: Mapped[str] = mapped_column(String(255))
