"""Маркер `ollama`: реальная модель не попадает в обычный прогон (1.2).

Хук проверяем напрямую: поднять вложенный pytest ради одного skip — дороже,
чем вызвать функцию с поддельным item.
"""

import importlib.util
from pathlib import Path


def load_conftest():
    path = Path(__file__).with_name("conftest.py")
    spec = importlib.util.spec_from_file_location("tests_conftest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeItem:
    def __init__(self, keywords: set[str]) -> None:
        self.keywords = keywords
        self.markers: list = []

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


def test_ollama_item_is_skipped_without_env():
    item = FakeItem({"ollama"})

    load_conftest().pytest_collection_modifyitems(None, [item])

    assert [marker.name for marker in item.markers] == ["skip"]


def test_ollama_item_runs_with_env(monkeypatch):
    monkeypatch.setenv("RUN_OLLAMA_TESTS", "1")
    item = FakeItem({"ollama"})

    load_conftest().pytest_collection_modifyitems(None, [item])

    assert item.markers == []


def test_regular_item_is_not_touched():
    item = FakeItem({"unit"})

    load_conftest().pytest_collection_modifyitems(None, [item])

    assert item.markers == []
