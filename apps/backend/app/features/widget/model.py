"""SQLAlchemy model for the Widget entity."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base


class Widget(Base):
    """Widget entity — the canonical CRUD example."""

    __tablename__ = "widgets"

    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
