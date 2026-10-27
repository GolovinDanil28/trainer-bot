"""Конфигурация приложения.

Читается один раз на старте: из переменных окружения и из `.env`.
Валидация здесь — единственная точка, где опечатка в конфиге должна падать
сразу, а не в момент первого напоминания в три часа ночи.
"""

import re
from datetime import time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Час — одна или две цифры: «9:00» в конфиге не должно требовать правки,
# но «21» без минут — уже не время вопроса.
_HH_MM_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")


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

    # Время вечернего вопроса о самочувствии в таймзоне пользователя.
    # Локальное «21:00», а не UTC-момент: вопрос — свойство дня, а не точки.
    wellness_time: str = "21:00"

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

    @field_validator("wellness_time")
    @classmethod
    def _wellness_time_is_hh_mm(cls, value: str) -> str:
        match = _HH_MM_RE.fullmatch(value.strip())
        if match is None:
            raise ValueError(
                f"WELLNESS_TIME должен быть в формате ЧЧ:ММ, получено '{value}'. "
                "Пример: 21:00"
            )

        hour, minute = int(match["hour"]), int(match["minute"])
        if hour > 23 or minute > 59:
            raise ValueError(
                f"WELLNESS_TIME вне диапазона 00:00–23:59, получено '{value}'. "
                "Пример: 21:00"
            )

        # Нормализуем к двум цифрам: «9:30» и «09:30» — одно и то же время.
        return f"{hour:02d}:{minute:02d}"

    @property
    def zone(self) -> ZoneInfo:
        """Таймзона для форматирования и вычисления моментов."""
        return ZoneInfo(self.tz)

    @property
    def wellness_at(self) -> time:
        """Время вечернего вопроса как `time`: формат уже проверен валидатором."""
        return time.fromisoformat(self.wellness_time)
