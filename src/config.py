"""Конфигурация приложения.

Читается один раз на старте: из переменных окружения и из `.env`.
Валидация здесь — единственная точка, где опечатка в конфиге должна падать
сразу, а не в момент первого напоминания в три часа ночи.
"""

from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки из окружения. Имена полей = имена переменных в нижнем регистре."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Лишние переменные в .env не должны ломать старт.
        extra="ignore",
    )

    # SecretStr, а не str: значение не попадает в repr, логи и трейсбеки.
    bot_token: SecretStr
    owner_id: int

    tz: str = "Asia/Yekaterinburg"
    db_url: str = "sqlite:///trainer.db"

    # None означает «до конца суток пользователя» (см. R-08).
    # Число нужно только для отладки, чтобы не ждать сутки.
    miss_window_hours: int | None = Field(default=None, ge=1)

    # Локальная модель. Проверяем только синтаксис: бот обязан подниматься
    # и работать со строгими командами, даже если Ollama не запущена (R-15).
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = Field(default="qwen2.5:7b", min_length=1)
    ollama_timeout_seconds: float = Field(default=20, gt=0)

    @field_validator("tz")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"неизвестная таймзона '{value}'. Пример корректной: Asia/Yekaterinburg"
            ) from exc
        return value

    @field_validator("ollama_url")
    @classmethod
    def _ollama_url_is_http(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                f"OLLAMA_URL должен быть http(s)-адресом с хостом, получено '{value}'. "
                "Пример: http://localhost:11434"
            )
        return value

    @property
    def zone(self) -> ZoneInfo:
        """Таймзона для форматирования и вычисления моментов."""
        return ZoneInfo(self.tz)
