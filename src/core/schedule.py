"""Домен расписания: типы тренировок, дни недели и разбор команды `/new`.

Telegram здесь нет: на вход строка, на выходе структура или `CommandError`
с текстом, который можно показать пользователю как есть.
"""

import re
from dataclasses import dataclass
from datetime import time
from enum import IntEnum, StrEnum

EXAMPLE = "/new strength Пн,Ср,Пт 19:30"

_TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")


class WorkoutType(StrEnum):
    """Типы тренировок v1. Свободный текст не поддерживаем: по типу потом
    считаются правила плана, а по строке их считать нельзя."""

    STRENGTH = "strength"
    CARDIO = "cardio"
    MOBILITY = "mobility"


class Weekday(IntEnum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


WEEKDAY_ALIASES: dict[str, Weekday] = {
    "пн": Weekday.MONDAY,
    "вт": Weekday.TUESDAY,
    "ср": Weekday.WEDNESDAY,
    "чт": Weekday.THURSDAY,
    "пт": Weekday.FRIDAY,
    "сб": Weekday.SATURDAY,
    "вс": Weekday.SUNDAY,
}

# Длительность по умолчанию: в команде `/new` её нет, а в напоминании (R-07)
# она должна быть. Значения можно будет менять отдельной командой.
DEFAULT_DURATION_MIN: dict[WorkoutType, int] = {
    WorkoutType.STRENGTH: 45,
    WorkoutType.CARDIO: 30,
    WorkoutType.MOBILITY: 20,
}

# Подписи для пользователя: в сообщениях английские значения enum не показываем.
WORKOUT_TYPE_LABELS: dict[WorkoutType, str] = {
    WorkoutType.STRENGTH: "силовая",
    WorkoutType.CARDIO: "кардио",
    WorkoutType.MOBILITY: "мобильность",
}

WEEKDAY_SHORT: dict[Weekday, str] = {
    Weekday.MONDAY: "Пн",
    Weekday.TUESDAY: "Вт",
    Weekday.WEDNESDAY: "Ср",
    Weekday.THURSDAY: "Чт",
    Weekday.FRIDAY: "Пт",
    Weekday.SATURDAY: "Сб",
    Weekday.SUNDAY: "Вс",
}


class CommandError(ValueError):
    """Ошибка ввода. Сообщение предназначено пользователю, а не в лог."""


@dataclass(frozen=True, slots=True)
class WorkoutDraft:
    """Готовая к сохранению тренировка — результат разбора `/new`."""

    workout_type: WorkoutType
    weekdays: tuple[Weekday, ...]
    time_of_day: time
    duration_min: int


@dataclass(frozen=True, slots=True)
class WorkoutView:
    """Тренировка, прочитанная из базы: то же, что draft, но с id.

    Отдельная структура, потому что до сохранения id нет. Функции показа
    принимают именно её — так нельзя случайно показать пользователю черновик
    как существующую тренировку.
    """

    id: int
    workout_type: WorkoutType
    weekdays: tuple[Weekday, ...]
    time_of_day: time
    duration_min: int


def parse_new_command(text: str) -> WorkoutDraft:
    """Разобрать `/new <тип> <дни> <время>` или бросить `CommandError`."""
    parts = text.split()
    if not parts:
        raise CommandError(f"Пустая команда. Пример: {EXAMPLE}")

    # `/new@TrainerBot` — обычная форма в группах, поэтому упоминание срезаем.
    command = parts[0].split("@", 1)[0].lower()
    if command != "/new":
        raise CommandError(f"Ожидалась команда /new, получено «{parts[0]}». Пример: {EXAMPLE}")

    args = parts[1:]
    if len(args) != 3:
        raise CommandError(
            f"Нужно три аргумента: тип, дни, время. Получено {len(args)}. Пример: {EXAMPLE}"
        )

    workout_type = _parse_type(args[0])
    weekdays = _parse_weekdays(args[1])
    time_of_day = _parse_time(args[2])

    return WorkoutDraft(
        workout_type=workout_type,
        weekdays=weekdays,
        time_of_day=time_of_day,
        duration_min=DEFAULT_DURATION_MIN[workout_type],
    )


def _parse_type(raw: str) -> WorkoutType:
    try:
        return WorkoutType(raw.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in WorkoutType)
        raise CommandError(
            f"Неизвестный тип тренировки «{raw}». Допустимые: {allowed}. Пример: {EXAMPLE}"
        ) from exc


def _parse_weekdays(raw: str) -> tuple[Weekday, ...]:
    allowed = ", ".join(WEEKDAY_ALIASES)
    days: set[Weekday] = set()

    for item in raw.split(","):
        key = item.strip().lower().rstrip(".")
        if not key:
            raise CommandError(f"Пустой день в списке «{raw}». Допустимые: {allowed}")
        if key not in WEEKDAY_ALIASES:
            raise CommandError(
                f"Неизвестный день «{item.strip()}». Допустимые: {allowed}. Пример: {EXAMPLE}"
            )
        days.add(WEEKDAY_ALIASES[key])

    # Сортировка делает результат предсказуемым: «Пт,Пн» и «Пн,Пт» — одно и то же.
    return tuple(sorted(days))


def _parse_time(raw: str) -> time:
    match = _TIME_RE.fullmatch(raw.strip())
    if match is None:
        raise CommandError(
            f"Время должно быть в формате ЧЧ:ММ, получено «{raw}». Пример: {EXAMPLE}"
        )

    hour, minute = int(match["hour"]), int(match["minute"])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CommandError(
            f"Время вне диапазона 00:00–23:59, получено «{raw}». Пример: {EXAMPLE}"
        )

    return time(hour=hour, minute=minute)
