"""Тесты конфигурации.

Все тесты создают Settings с `_env_file=None`: иначе они прочитали бы
настоящий .env разработчика и результат зависел бы от машины.
"""

from datetime import time

import pytest
from pydantic import ValidationError

from src.config import Settings

_ENV_NAMES = (
    "BOT_TOKEN",
    "OWNER_ID",
    "TZ",
    "DB_URL",
    "MISS_WINDOW_HOURS",
    "OLLAMA_URL",
    "OLLAMA_MODEL",
    "OLLAMA_TIMEOUT_SECONDS",
    "WELLNESS_TIME",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Убрать переменные окружения: иначе тест зависит от `.env` машины.

    `_env_file=None` отключает только файл, а `os.environ` pydantic-settings
    читает всё равно — при запуске из каталога с загруженным `.env` тесты на
    дефолты падали бы «на пустом месте».
    """
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


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


def test_ollama_defaults():
    settings = make_settings()

    assert settings.ollama_url == "http://localhost:11434"
    assert settings.ollama_model == "qwen2.5:7b"
    assert settings.ollama_timeout_seconds == 20


@pytest.mark.parametrize(
    "url",
    ["localhost:11434", "ftp://localhost:11434", "http://", ""],
    ids=["без-схемы", "чужая-схема", "без-хоста", "пусто"],
)
def test_ollama_url_requires_http_scheme_and_host(url):
    with pytest.raises(ValidationError, match="OLLAMA_URL"):
        make_settings(ollama_url=url)


@pytest.mark.parametrize("timeout", [0, -1], ids=["ноль", "отрицательный"])
def test_ollama_timeout_must_be_positive(timeout):
    with pytest.raises(ValidationError):
        make_settings(ollama_timeout_seconds=timeout)


def test_ollama_model_must_not_be_empty():
    with pytest.raises(ValidationError):
        make_settings(ollama_model="")


def test_wellness_time_default_is_evening():
    settings = make_settings()

    assert settings.wellness_time == "21:00"
    assert settings.wellness_at == time(21, 0)


def test_wellness_time_accepts_single_digit_hour_and_normalizes():
    settings = make_settings(wellness_time="9:05")

    assert settings.wellness_time == "09:05"
    assert settings.wellness_at == time(9, 5)


@pytest.mark.parametrize(
    "value",
    ["25:00", "21-00", "", "21", "21:60", "вечер"],
    ids=[
        "час-вне-диапазона",
        "чужой-разделитель",
        "пусто",
        "без-минут",
        "минуты-вне-диапазона",
        "словами",
    ],
)
def test_wellness_time_requires_hh_mm(value):
    with pytest.raises(ValidationError, match="WELLNESS_TIME"):
        make_settings(wellness_time=value)
