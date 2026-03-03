"""
Модель пользователей.
Локальные пароли больше не хранятся, аутентификация через Auth0.
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from models.database import Base


class User(Base):
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True)
    # sub из Auth0, например 'yandex|123456789'
    username   = Column(String(100), unique=True, nullable=False)
    email      = Column(String(120), unique=True, nullable=True)
    full_name  = Column(String(100), default="")
    role       = Column(String(20), default="user")   # admin | manager | user
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
