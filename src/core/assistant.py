"""Чистая часть слоя естественного языка: парсинг и валидация ответа модели.

Содержит:
- `AssistantAction` — перечисление действий (create, list, off, none).
- `AssistantIntent` — результат парсинга: действие + тип/дни/время/id + причина.
- `IntentError` — выбрасывается, когда ответ модели невалиден.
- `parse_intent(raw_json)` — функция, которая принимает JSON-строку от модели
  и возвращает структурированное намерение или поднимает ошибку.

Никто Besides `bot/assistant_service.py` импортирует этот модуль напрямую;
все остальное взаимодействие идет через протокол `IntentTranslator`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

from src.core.schedule import WEEKDAY_ALIASES, Weekday, WorkoutType


class IntentError(Exception):
    """Ошибка валидации ответа модели."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class AssistantAction(StrEnum):
    CREATE = "create"
    LIST = "list"
    OFF = "off"
    NONE = "none"


@dataclass
class AssistantIntent:
    action: AssistantAction
    type: WorkoutType | None = None
    days: list[Weekday] | None = None
    time: str | None = None  # "ЧЧ:ММ"
    workout_id: int | None = None
    reason: str = ""


# Минимальный словарь полных названий дней для нормализации,
# WEEKDAY_ALIASES в core/schedule.py имеет только короткие формы.
_FULL_DAY_ALIASES: dict[str, Weekday] = {
    "понедельник": Weekday.MONDAY,
    "вторник": Weekday.TUESDAY,
    "среда": Weekday.WEDNESDAY,
    "четверг": Weekday.THURSDAY,
    "пятница": Weekday.FRIDAY,
    "суббота": Weekday.SATURDAY,
    "воскресенье": Weekday.SUNDAY,
}


def _parse_day(raw: str) -> Weekday:
    """Нормализовать название дня: короткая форма (Пн, вт) или полная (понедельник)."""
    key = raw.strip().lower().rstrip(".")
    # Сначала пробуем существующие алиасы из core/schedule.py
    if key in WEEKDAY_ALIASES:
        return WEEKDAY_ALIASES[key]
    # Если не нашли — пробуем полные названия
    if key in _FULL_DAY_ALIASES:
        return _FULL_DAY_ALIASES[key]
    # Если итем не найден — поднимаем ошибку
    allowed_short = ", ".join(WEEKDAY_ALIASES.keys())
    allowed_full = ", ".join(_FULL_DAY_ALIASES.keys())
    raise IntentError(
        f"Неизвестный день «{raw}». Допустимые короткие: {allowed_short}, полные: {allowed_full}"
    )


def _parse_time(raw: str) -> str:
    """Валидировать формат ЧЧ:ММ и вернуть нормализованную строку."""
    match = re.fullmatch(r"(\d{2}):(\d{2})", raw.strip())
    if not match:
        raise IntentError(
            f"Время должно быть в формате ЧЧ:ММ, получено «{raw}»."
        )
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise IntentError(
            f"Время вне допустимого диапазона: «{raw}». Часы 00–23, минуты 00–59."
        )
    return f"{hour:02d}:{minute:02d}"


def _validate_days(days_raw: object) -> list[Weekday]:
    """Валидировать и нормализовать список дней.

    Возвращает отсортированный список уникальных Weekday.
    """
    if not isinstance(days_raw, list):
        raise IntentError(
            f"Дни должны быть списком, получено {type(days_raw).__name__}"
        )
    if not days_raw:
        raise IntentError("Список дней не может быть пустым для действия create")
    result: set[Weekday] = set()
    for day_raw in days_raw:
        if not isinstance(day_raw, str):
            raise IntentError(f"День должен быть строкой, получено {type(day_raw).__name__}")
        result.add(_parse_day(day_raw))
    # Сортировка делает результат предсказуемым: «Пт,Пн» и «Пн,Пт» — одно и то же
    return sorted(result)


def parse_intent(raw_json: str) -> AssistantIntent:
    """Парсить JSON-ответ модели и возвращать структурированное намерение.

    Args:
        raw_json: Строка, которую модель вернула в поле content (format: json).

    Returns:
        AssistantIntent с распарленными полями.

    Raises:
        IntentError: если JSON невалиден, не хватает обязательных полей
        или значения не соответствуют требованиям.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise IntentError(f"Ответ модели не является валидным JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise IntentError("Ответ модели должен быть JSON-объектом")

    action_raw = data.get("action")
    if not isinstance(action_raw, str):
        raise IntentError("Поле «action» должно быть строкой")

    try:
        action = AssistantAction(action_raw)
    except ValueError:
        allowed = ", ".join(item.value for item in AssistantAction)
        raise IntentError(
            f"Неизвестное действие «{action_raw}». Допустимые: {allowed}."
        ) from None

    # Чтение полей из JSON (могут отсутствовать для некоторых действий)
    ar_type = data.get("type")
    ar_days = data.get("days")
    ar_time = data.get("time")
    ar_workout_id = data.get("workout_id")
    ar_reason = data.get("reason", "")

    # Приведение типа workout_type, если указано
    wtype: WorkoutType | None = None
    if ar_type is not None:
        try:
            wtype = WorkoutType(ar_type.strip().lower())
        except ValueError:
            allowed = ", ".join(item.value for item in WorkoutType)
            raise IntentError(
                f"Неизвестный тип тренировки «{ar_type}». Допустимые: {allowed}."
            ) from None

    # Валидация зависит от действия
    if action == AssistantAction.CREATE:
        if wtype is None:
            raise IntentError(
                "Для действия create обязательно поле type (strength/cardio/mobility)."
            )
        if ar_days is None:
            raise IntentError("Для действия create обязательно поле days.")
        days = _validate_days(ar_days)
        if ar_time is None:
            raise IntentError("Для действия create обязательно поле time.")
        time = _parse_time(ar_time)
        # При create workout_id должен бытьNone (пользователь не задаёт id вручную)
        if ar_workout_id is not None:
            raise IntentError(
                "При	create workout_id должен быть отсутствующим (авто-генерация)."
            )
        workout_id: int | None = None
    elif action == AssistantAction.LIST:
        # Для list тип, дни, время и id должны быть absent/null; лишние поля
        # игнорируем, чтобы не ломать простые случаи.
        days: list[Weekday] | None = None
        time: str | None = None
        workout_id: int | None = None
    elif action == AssistantAction.OFF:
        if not isinstance(ar_workout_id, int) or ar_workout_id <= 0:
            raise IntentError(
                "Для действия off обязательно положительное целое workout_id."
            )
        days: list[Weekday] | None = None
        time: str | None = None
        workout_id: int = ar_workout_id
    elif action == AssistantAction.NONE:
        # При none ожидаем, что type, days, time, workout_id будут None/null.
        # Если модель прислала лишние поля — не критически, игнорируем их.
        days: list[Weekday] | None = None
        time: str | None = None
        workout_id: int | None = None
    else:
        # Недостижимый случай, так как action проверяется через enum выше
        raise IntentError("Внутренняя ошибка: неизвестное действие после проверки enum")

    return AssistantIntent(
        action=action,
        type=wtype,
        days=days,
        time=time,
        workout_id=workout_id,
        reason=str(ar_reason) if ar_reason is not None else "",
    )