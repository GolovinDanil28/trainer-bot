"""Тесты конфигурации.

Все тесты создают Settings с `_env_file=None`: иначе они прочитали бы
настоящий .env разработчика и результат зависел бы от машины.
"""

import pytest
from pydantic import ValidationError

from src.config import Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"bot_token": "123:test", "owner_id": 42}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_valid_config_gives_zone():
    settings = make_settings()

    assert settings.tz == "Asia/Yekaterinburg"
    assert settings.zone.key == "Asia/Yekaterinburg"


def test_invalid_timezone_fails_at_start():
    with pytest.raises(ValidationError, match="неизвестная таймзона"):
        make_settings(tz="Mars/Olympus")


def test_missing_token_fails_at_start():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, owner_id=42)  # type: ignore[call-arg]


def test_token_is_not_printed_in_repr():
    settings = make_settings()

    assert "123:test" not in repr(settings)
    assert settings.bot_token.get_secret_value() == "123:test"


def test_miss_window_default_is_end_of_day():
    # None = до конца суток пользователя (R-08), число — только для отладки.
    assert make_settings().miss_window_hours is None


def test_negative_miss_window_rejected():
    with pytest.raises(ValidationError):
        make_settings(miss_window_hours=0)
