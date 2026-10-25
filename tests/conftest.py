"""Общие фикстуры: настоящая SQLite-база в tmp_path, без моков хранилища."""

import os
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


def _skip_ollama_items(items: list, enabled: bool) -> None:
    """Пометить тесты с маркером `ollama` пропущенными, если модель не включена."""
    if enabled:
        return
    skip = pytest.mark.skip(
        reason="нужен RUN_OLLAMA_TESTS=1 и запущенная Ollama с qwen2.5:7b"
    )
    for item in items:
        if "ollama" in item.keywords:
            item.add_marker(skip)


def pytest_collection_modifyitems(config, items: list) -> None:
    # Обычный прогон не должен зависеть от того, запущена ли Ollama на машине.
    _skip_ollama_items(items, enabled=os.getenv("RUN_OLLAMA_TESTS") == "1")
