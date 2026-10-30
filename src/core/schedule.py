"""Домен расписания: типы тренировок, дни недели и разбор команды `/new`.

Telegram здесь нет: на вход строка, на выходе структура или `CommandError`
с текстом, который можно показать пользователю как есть.
"""

import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from enum import IntEnum, StrEnum

EXAMPLE = "/new strength Пн,Ср,Пт 19:30"

_TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")


class WorkoutType(StrEnum):
    """Известные типы v1 — словарь умолчаний, а не список допуска.

    С R-33 тип тренировки — свободная фраза («бег», «интервальный бег»):
    по типу не считаются правила плана, поэтому строка допустима. Enum
    остаётся ради длительности по умолчанию и русских подписей.
    """

    STRENGTH = "strength"
    CARDIO = "cardio"
    MOBILITY = "mobility"


# Границы свободного типа (R-33): короче — не тип, длиннее — уже описание.
MIN_TYPE_LENGTH = 2
MAX_TYPE_LENGTH = 32

# Служебные символы Telegram-разметки: тип не должен уметь ломать сообщение.
_TYPE_FORBIDDEN = re.compile(r"[*_`\[\]<>#|]")


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
DEFAULT_DURATION_MIN: dict[str, int] = {
    WorkoutType.STRENGTH: 45,
    WorkoutType.CARDIO: 30,
    WorkoutType.MOBILITY: 20,
}

# Свободный тип без своей длительности (R-33): 30 минут — середина шкалы.
UNKNOWN_TYPE_DURATION_MIN = 30

# Темп пересчёта дистанции в минуты (R-32). Один раз на входе, без обратного
# пересчёта: 7 км → 70 минут, а «7 км» в списке остаётся дистанцией.
PACE_MIN_PER_KM = 10

# Шаг округления длительности вниз и нижняя граница. Живут здесь, потому что
# нужны и при создании (R-32), и в правилах плана (plan.py импортирует эти
# константы — обратный импорт дал бы цикл).
DURATION_STEP_MIN = 5
MIN_DURATION_MIN = 5

# Подписи для известных типов: в сообщениях английские значения enum не
# показываем. Свободный тип показывается как написан владельцем.
WORKOUT_TYPE_LABELS: dict[str, str] = {
    WorkoutType.STRENGTH: "силовая",
    WorkoutType.CARDIO: "кардио",
    WorkoutType.MOBILITY: "мобильность",
}


def workout_label(workout_type: str) -> str:
    """Русская подпись известного типа или сам тип, если он свободный."""
    return WORKOUT_TYPE_LABELS.get(str(workout_type), str(workout_type))


def normalize_workout_type(raw: str) -> str:
    """Проверить свободный тип (R-33) и вернуть его в каноничном виде.

    Схлопываем пробелы: «интервальный   бег» и «интервальный бег» — один тип.
    """
    # Нижний регистр — часть канона: «CARDIO» и «cardio» один тип (R-33).
    value = " ".join(str(raw).split()).lower()
    if not value:
        raise CommandError("Не назван тип тренировки. Пример: " + EXAMPLE)
    if len(value) < MIN_TYPE_LENGTH:
        raise CommandError(f"Слишком короткий тип «{value}»: нужно минимум 2 символа.")
    if len(value) > MAX_TYPE_LENGTH:
        raise CommandError(
            f"Слишком длинный тип: {len(value)} символов при максимуме {MAX_TYPE_LENGTH}."
        )
    if _TYPE_FORBIDDEN.search(value):
        raise CommandError(
            f"Тип «{value}» содержит служебные символы. Назови вид спорта словами."
        )
    return value


def duration_for(
    workout_type: str, distance_km: float | None, duration_min: int | None = None
) -> int:
    """Длительность новой тренировки: сказанная явно, из дистанции или умолчание.

    Явные минуты сильнее дистанции: «7 км на 40 минут» — это просьба, а не
    подсказка, и пересчитывать её нельзя (R-32).
    """
    if duration_min is not None:
        return duration_min
    if distance_km is not None:
        return duration_from_distance(distance_km)
    return DEFAULT_DURATION_MIN.get(str(workout_type), UNKNOWN_TYPE_DURATION_MIN)


def duration_from_distance(distance_km: float) -> int:
    """Дистанция → минуты по фиксированному темпу, вниз до 5-минутного шага."""
    raw = distance_km * PACE_MIN_PER_KM
    stepped = int(raw // DURATION_STEP_MIN) * DURATION_STEP_MIN
    return max(MIN_DURATION_MIN, stepped)

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
    """Готовая к сохранению тренировка — результат разбора команды или фразы."""

    workout_type: str
    weekdays: tuple[Weekday, ...]
    time_of_day: time
    duration_min: int
    distance_km: float | None = None
    date_once: date | None = None
    time_once: time | None = None


@dataclass(frozen=True, slots=True)
class WorkoutView:
    """Тренировка, прочитанная из базы: то же, что draft, но с id.

    Отдельная структура, потому что до сохранения id нет. Функции показа
    принимают именно её — так нельзя случайно показать пользователю черновик
    как существующую тренировку.
    """

    id: int
    workout_type: str
    weekdays: tuple[Weekday, ...]
    time_of_day: time
    duration_min: int
    distance_km: float | None = None
    date_once: date | None = None
    time_once: time | None = None


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

    return make_weekly_draft(workout_type, weekdays, time_of_day)


def make_weekly_draft(
    workout_type: str,
    weekdays: tuple[Weekday, ...],
    time_of_day: time,
    distance_km: float | None = None,
    duration_min: int | None = None,
) -> WorkoutDraft:
    """Собрать недельную тренировку из уже проверенных значений.

    Общая точка для `/new` и свободного текста: длительность считается одной и
    той же функцией, поэтому два пути не могут разойтись в правилах (R-14).
    """
    if not weekdays:
        raise CommandError(
            "Не названы дни недели. Пример: " + EXAMPLE
        )
    return WorkoutDraft(
        workout_type=normalize_workout_type(workout_type),
        weekdays=weekdays,
        time_of_day=time_of_day,
        duration_min=duration_for(workout_type, distance_km, duration_min),
        distance_km=distance_km,
    )


def _parse_type(raw: str) -> str:
    """Тип из `/new`: свободная фраза проверяется теми же границами, что R-33."""
    return normalize_workout_type(raw)


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


# Дата, названная словами, которую модель возвращает как токен (D2).
_RELATIVE_DAYS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_DATE_NUMERIC = re.compile(r"^(?P<day>\d{1,2})\.(?P<month>\d{1,2})(?:\.(?P<year>\d{4}))?$")
DATE_RAW_MAX_LENGTH = 16


def resolve_date(raw: str, today: date) -> date | None:
    """Токен даты → календарная дата в таймзоне владельца (R-35, D2).

    Без года берётся ближайшая такая дата в будущем: «28.10» в декабре — это
    следующая осень, а не прошедшая. С явным годом прошедшая дата — отказ
    (`None`): владелец назвал конкретику, и угадывать за него нельзя.
    """
    value = " ".join(str(raw).split()).lower()
    if value in _RELATIVE_DAYS:
        return today + timedelta(days=_RELATIVE_DAYS[value])

    match = _DATE_NUMERIC.fullmatch(value)
    if match is None:
        return None

    day, month = int(match["day"]), int(match["month"])
    year_raw = match["year"]
    try:
        if year_raw is not None:
            explicit = date(int(year_raw), month, day)
            return explicit if explicit >= today else None
        candidate = date(today.year, month, day)
    except ValueError:
        # 31.02 и подобное — не дата, а опечатка.
        return None

    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def make_once_draft(
    workout_type: str,
    day: date,
    time_of_day: time,
    distance_km: float | None = None,
    duration_min: int | None = None,
) -> WorkoutDraft:
    """Разовая тренировка на дату (R-35): дата и время вместо дней недели."""
    return WorkoutDraft(
        workout_type=normalize_workout_type(workout_type),
        weekdays=(),
        time_of_day=time_of_day,
        duration_min=duration_for(workout_type, distance_km, duration_min),
        distance_km=distance_km,
        date_once=day,
        time_once=time_of_day,
    )
