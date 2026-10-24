"""Базовый класс моделей.

Отдельный модуль, а не `session.py`: модели импортируют `Base` отсюда, и не
тянут за собой движок и подключение к базе.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общая метадата всех таблиц проекта."""
