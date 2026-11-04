"""Пользователь как доменное понятие: профиль и регистрация (R-41, R-44).

Ни Telegram, ни БД: только данные, которые нужны планировщику и доступу.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class UserProfile:
    """Всё, что планировщику нужно знать о человеке."""

    id: int
    telegram_id: int
    tz: str
    wellness_time: str
    week_review_time: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class Registration:
    """Правила входа: многоразовый код и настройки по умолчанию."""

    invite_code: str
    tz: str
    wellness_time: str
    week_review_time: str

    @property
    def enabled(self) -> bool:
        """Пустой код отключает регистрацию: остаётся только владелец."""
        return bool(self.invite_code)


class UserSettingsError(ValueError):
    """Ошибка ввода `/settings`. Текст предназначен пользователю."""


@dataclass(frozen=True, slots=True)
class SettingsUpdate:
    """Что именно меняем: `None` — не трогаем."""

    tz: str | None = None
    wellness_time: str | None = None
    week_review_time: str | None = None


_TIME = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})$")


def _parse_time(value: str, name: str) -> str:
    match = _TIME.fullmatch(value.strip())
    if match is None:
        raise UserSettingsError(
            f"{name} должно быть в формате ЧЧ:ММ, получено «{value}». Пример: 21:30"
        )
    hour, minute = int(match["hour"]), int(match["minute"])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise UserSettingsError(
            f"{name} вне диапазона 00:00–23:59, получено «{value}»."
        )
    return f"{hour:02d}:{minute:02d}"


def parse_settings_command(args: Sequence[str]) -> SettingsUpdate | None:
    """Разобрать хвост `/settings`. `None` — показать текущие значения (R-45).

    Одна подкоманда за вызов: частичное обновление, остальное не сбрасывается.
    """
    if not args:
        return None
    key = args[0].lower()
    if len(args) != 2:
        raise UserSettingsError(
            "Нужно значение: `/settings tz Asia/Yekaterinburg`, "
            "`/settings wellness 21:30` или `/settings week 20:00`."
        )
    value = args[1]

    if key == "tz":
        try:
            ZoneInfo(value.strip())
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise UserSettingsError(
                f"Неизвестная таймзона «{value}». Пример: Asia/Yekaterinburg."
            ) from exc
        return SettingsUpdate(tz=value.strip())
    if key == "wellness":
        return SettingsUpdate(wellness_time=_parse_time(value, "Время вопроса"))
    if key == "week":
        return SettingsUpdate(week_review_time=_parse_time(value, "Время разбора"))

    raise UserSettingsError(
        "Неизвестная настройка. Доступно: tz, wellness, week."
    )
