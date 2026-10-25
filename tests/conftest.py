"""Общие фикстуры: настоящая SQLite-база в tmp_path, без моков хранилища."""

from zoneinfo import ZoneInfo

import pytest

from src.bot.service import WorkoutService
from src.db.session import create_db_engine, init_db, make_session_factory


@pytest.fixture
def engine(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'trainer.db'}")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def service(session_factory) -> WorkoutService:
    # Зона как у реального пользователя: тесты должны ловить ошибки перевода
    # времени, а не проходить только в UTC.
    return WorkoutService(session_factory, ZoneInfo("Asia/Yekaterinburg"))
