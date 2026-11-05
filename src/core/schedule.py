"""Домен расписания: типы тренировок, дни недели и разбор команды `/new`.

Telegram здесь нет: на вход строка, на выходе структура или `CommandError`
с текстом, который можно показать пользователю как есть.
"""

import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from enum import IntEnum, StrEnum

EXAMPLE = "/new бег 10км Пт 19:30"
MOVE_EXAMPLE = "/move 2 20:00"
EDIT_EXAMPLE = "/edit 2 days Пн,Ср"

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

_FULL_DAY_ROOTS: tuple[tuple[str, Weekday], ...] = (
    ("понед", Weekday.MONDAY),
    ("вторн", Weekday.TUESDAY),
    ("сред", Weekday.WEDNESDAY),
    ("четвер", Weekday.THURSDAY),
    ("пятн", Weekday.FRIDAY),
    ("суббот", Weekday.SATURDAY),
    ("воскрес", Weekday.SUNDAY),
)

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


# Границы длительности из команды: 5 минут — нижняя граница плана, 600 —
# десять часов; выше начинается «две тренировки в одной строке».
MIN_DURATION_ARG_MIN = 5
MAX_DURATION_ARG_MIN = 600


_DISTANCE = re.compile(r"^(?P<value>\d+(?:[.,]\d+)?)(?:км|km)$", re.IGNORECASE)
MAX_COMMAND_DISTANCE_KM = 100.0


def _number(raw: str) -> float:
    return float(raw.replace(",", "."))


def _extract_distance(args: list[str]) -> tuple[list[str], float | None]:
    """Вынуть дистанцию из любого места после типа (R-01).

    `10км`, `10.5км`, `10 км`, `10 km`. Одиночное число дистанцией не считается:
    `10` — это время или минуты, иначе позиции начнут путаться.
    """
    rest: list[str] = []
    distance: float | None = None
    index = 0
    while index < len(args):
        token = args[index]
        match = _DISTANCE.fullmatch(token)
        value: float | None = None
        if match is not None:
            value = _number(match["value"])
        elif (
            token.replace(",", ".").replace(".", "").isdigit()
            and index + 1 < len(args)
            and args[index + 1].lower() in {"км", "km"}
        ):
            value = _number(token)
            index += 1
        if value is None:
            rest.append(token)
            index += 1
            continue

        if distance is not None:
            raise CommandError("Дистанция указана дважды. Пример: " + EXAMPLE)
        if not 0 < value <= MAX_COMMAND_DISTANCE_KM:
            raise CommandError(
                f"Дистанция должна быть больше 0 и не больше "
                f"{MAX_COMMAND_DISTANCE_KM:g} км, получено {value:g}. Пример: {EXAMPLE}"
            )
        distance = value
        index += 1
    return rest, distance


def _join_day_prepositions(args: list[str]) -> list[str]:
    """«по пятницам» и «в пятницу» — предлог относится к дню, а не к типу."""
    result: list[str] = []
    index = 0
    while index < len(args):
        token = args[index].lower()
        if (
            token in {"по", "в"}
            and index + 1 < len(args)
            and _day(token=args[index + 1]) is not None
        ):
            result.append(args[index + 1])
            index += 2
            continue
        result.append(args[index])
        index += 1
    return result


def _day(token: str) -> Weekday | None:
    key = token.strip().lower().rstrip(".")
    if key in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[key]
    for root, day in _FULL_DAY_ROOTS:
        if key.startswith(root):
            return day
    return None


def parse_new_command(text: str) -> WorkoutDraft:
    """Разобрать `/new <тип> <дни> <время> [минуты]` или бросить `CommandError`."""
    parts = text.split()
    if not parts:
        raise CommandError(f"Пустая команда. Пример: {EXAMPLE}")

    # `/new@TrainerBot` — обычная форма в группах, поэтому упоминание срезаем.
    command = parts[0].split("@", 1)[0].lower()
    if command != "/new":
        raise CommandError(f"Ожидалась команда /new, получено «{parts[0]}». Пример: {EXAMPLE}")

    args, distance_km = _extract_distance(parts[1:])
    args = _join_day_prepositions(args)
    if len(args) not in (3, 4):
        raise CommandError(
            "Нужно: тип, дни и время; дистанция — например, 10км; "
            f"длительность — необязательно. Получено {len(args)}. Пример: {EXAMPLE}"
        )

    workout_type = _parse_type(args[0])
    weekdays = _parse_weekdays(args[1])
    time_of_day = _parse_time(args[2])
    duration_min = _parse_duration_arg(args[3]) if len(args) == 4 else None

    return make_weekly_draft(
        workout_type,
        weekdays,
        time_of_day,
        distance_km=distance_km,
        duration_min=duration_min,
    )


@dataclass(frozen=True, slots=True)
class MoveDraft:
    """Готовый к сохранению перенос времени."""

    workout_id: int
    time_of_day: time


def parse_move_command(text: str) -> MoveDraft:
    """Разобрать `/move <номер> <время>` или бросить `CommandError`."""
    parts = text.split()
    command = parts[0].split("@", 1)[0].lower() if parts else ""
    if command != "/move":
        raise CommandError(f"Ожидалась команда /move. Пример: {MOVE_EXAMPLE}")
    args = parts[1:]
    if len(args) != 2:
        raise CommandError(
            f"Нужны номер тренировки и время. Получено {len(args)}. "
            f"Пример: {MOVE_EXAMPLE}"
        )
    raw_id, raw_time = args
    if not raw_id.isdigit() or int(raw_id) <= 0:
        raise CommandError(
            f"Номер тренировки должен быть положительным числом, получено «{raw_id}». "
            f"Пример: {MOVE_EXAMPLE}"
        )
    try:
        moment = _parse_time(raw_time)
    except CommandError as exc:
        raise CommandError(f"{exc} Пример: {MOVE_EXAMPLE}") from exc
    return MoveDraft(workout_id=int(raw_id), time_of_day=moment)


@dataclass(frozen=True, slots=True)
class EditDraft:
    """Что именно меняем у тренировки. Заполнено ровно одно поле."""

    workout_id: int
    workout_type: str | None = None
    weekdays: tuple[Weekday, ...] | None = None
    date_raw: str | None = None
    time_of_day: time | None = None


def parse_edit_command(text: str) -> EditDraft:
    """Разобрать `/edit <номер> <поле> <значение>` (R-48)."""
    parts = text.split()
    command = parts[0].split("@", 1)[0].lower() if parts else ""
    if command != "/edit":
        raise CommandError(f"Ожидалась команда /edit. Пример: {EDIT_EXAMPLE}")
    args = parts[1:]
    if len(args) < 3:
        raise CommandError(
            f"Нужно: номер, поле и значение. Пример: {EDIT_EXAMPLE}"
        )
    raw_id, field, *rest = args
    if not raw_id.isdigit() or int(raw_id) <= 0:
        raise CommandError(f"Номер тренировки должен быть положительным. Пример: {EDIT_EXAMPLE}")

    field = field.lower()
    if field == "type":
        # Тип — свободная фраза, поэтому значение может быть из двух слов.
        value = " ".join(rest)
        try:
            return EditDraft(int(raw_id), workout_type=normalize_workout_type(value))
        except CommandError as exc:
            raise CommandError(f"{exc} Пример: {EDIT_EXAMPLE}") from exc
    if len(rest) != 1 and field != "days":
        raise CommandError(
            f"Для поля «{field}» нужно одно значение. Пример: {EDIT_EXAMPLE}"
        )
    value = rest[0]
    if field == "days":
        joined = " ".join(_join_day_prepositions(rest))
        return EditDraft(int(raw_id), weekdays=_parse_weekdays(joined))
    if field == "date":
        return EditDraft(int(raw_id), date_raw=value)
    if field == "time":
        try:
            return EditDraft(int(raw_id), time_of_day=_parse_time(value))
        except CommandError as exc:
            raise CommandError(f"{exc} Пример: {EDIT_EXAMPLE}") from exc
    raise CommandError(
        f"Неизвестное поле «{field}». Доступно: type, days, date, time. "
        f"Пример: {EDIT_EXAMPLE}"
    )


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


_DURATION_HOURS = re.compile(
    r"^(?:(?P<hours>\d{1,2})ч(?:(?P<minutes>\d{1,2}))?"
    r"|(?P<h>\d{1,2}):(?P<m>\d{2})"
    r"|(?P<plain>\d{1,3}))$",
    re.IGNORECASE,
)


def _parse_duration_arg(raw: str) -> int:
    """Длительность из четвёртого аргумента (R-01).

    Три записи с одним смыслом: `40` — минуты, `1ч30`/`2ч` — часы,
    `1:30` — `Ч:ММ`. Всё нормализуется в минуты до проверки границ,
    иначе `1ч70` молча стало бы «2 часа 10 минут».
    """
    value = raw.strip().lower()
    match = _DURATION_HOURS.fullmatch(value)
    if match is None:
        raise CommandError(
            f"Длительность должна быть минутами (40), часами (1ч30) или Ч:ММ (1:30), "
            f"получено «{raw}». Пример: {EXAMPLE}"
        )

    if match["plain"] is not None:
        minutes = int(match["plain"])
    elif match["h"] is not None:
        minutes = int(match["h"]) * 60 + int(match["m"])
    else:
        hours = int(match["hours"])
        rest = int(match["minutes"] or 0)
        if rest > 59:
            raise CommandError(
                f"В длительности «{raw}» больше 59 минут: запиши как 2ч10 или 130."
            )
        minutes = hours * 60 + rest

    if not MIN_DURATION_ARG_MIN <= minutes <= MAX_DURATION_ARG_MIN:
        raise CommandError(
            f"Длительность вне диапазона {MIN_DURATION_ARG_MIN}–"
            f"{MAX_DURATION_ARG_MIN} минут, получено {minutes}. Пример: {EXAMPLE}"
        )
    return minutes


def _parse_weekdays(raw: str) -> tuple[Weekday, ...]:
    allowed = ", ".join(WEEKDAY_ALIASES)
    days: set[Weekday] = set()

    for item in raw.split(","):
        key = item.strip().lower().rstrip(".")
        if not key:
            raise CommandError(f"Пустой день в списке «{raw}». Допустимые: {allowed}")
        day = _day(key)
        if day is None:
            raise CommandError(
                f"Неизвестный день «{item.strip()}». Допустимые: {allowed}, "
                f"а также полные названия (пятница). Пример: {EXAMPLE}"
            )
        days.add(day)

    # Сортировка делает результат предсказуемым: «Пт,Пн» и «Пн,Пт» — одно и то же.
    return tuple(sorted(days))


def _parse_time(raw: str) -> time:
    """Время: `ЧЧ:ММ` или часы без минут (`20` = 20:00), 0–23 (R-01)."""
    value = raw.strip()
    match = _TIME_RE.fullmatch(value)
    if match is not None:
        hour, minute = int(match["hour"]), int(match["minute"])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise CommandError(
                f"Время вне диапазона 00:00–23:59, получено «{raw}». Пример: {EXAMPLE}"
            )
        return time(hour=hour, minute=minute)

    if value.isdigit() and len(value) <= 2:
        # «в 20» — это 20:00: минуты не названы, значит ноль.
        hour = int(value)
        if 0 <= hour <= 23:
            return time(hour=hour, minute=0)

    raise CommandError(
        f"Время должно быть ЧЧ:ММ или часом без минут, получено «{raw}». "
        f"Пример: {EXAMPLE}"
    )


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
