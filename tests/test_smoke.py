"""Smoke-тест каркаса: пакеты существуют и импортируются без Telegram."""

import src.core
from src import __version__


def test_package_has_version():
    assert __version__ == "0.1.0"


def test_core_package_is_importable():
    # Импорт src.core без установленного telegram — это проверка правила 8.1:
    # домен не должен тянуть за собой bot/.
    assert src.core is not None
