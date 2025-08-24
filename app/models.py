from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer
from .db import Base

class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
