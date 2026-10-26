"""Чистая часть слоя естественного языка: парсинг и валидация ответа модели.

Содержит:
- `AssistantAction` — перечисление действий (create, list, off, none).
- `AssistantIntent` — результат парсинга: действие + тип/дни/время/id + причина.
- `IntentError` — выбрасывается, когда ответ модели невалиден.
- `parse_intent(raw_json)` — функция, которая принимает JSON-строку от модели
  и возвращает структурированное намерение или поднимает ошибку.

Модуль не знает ни про Telegram, ни про сеть: промпты и разбор ответа —
чистые функции, поэтому проверяются без Ollama.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum

from src.core.schedule import WEEKDAY_ALIASES, Weekday, WorkoutType

# Проверка «опоры на текст»: модель не имеет права добавлять поля, которых
# в фразе нет. Это дешевле и надёжнее, чем надеяться на 7B-модель (R-15).
_TYPE_WORDS: dict[WorkoutType, re.Pattern[str]] = {
    WorkoutType.STRENGTH: re.compile(r"\b(силов\w*|strength)\b", re.IGNORECASE),
    WorkoutType.CARDIO: re.compile(r"\b(кардио|cardio)\b", re.IGNORECASE),
    WorkoutType.MOBILITY: re.compile(
        r"\b(мобильн\w*|растяжк\w*|mobility)\b", re.IGNORECASE
    ),
}
_WEEKDAY_WORD = re.compile(
    r"\b(пн|вт|ср|чт|пт|сб|вс|понед\w*|вторн\w*|сред\w*|четвер\w*|"
    r"пятн\w*|суббот\w*|воскрес\w*)\b",
    re.IGNORECASE,
)
_EVERY_DAY = re.compile(
    r"\bкажд\w*\s+(день|дня|вечер\w*)\b|\bежедневн\w*\b", re.IGNORECASE
)
_TIME_WORD = re.compile(
    # Точное время: цифры ЧЧ:ММ, отдельное число, числительное или «половина».
    # Одно «утром» точным временем не считается — иначе модель подставит 08:00.
    r"\d{1,2}[:.]\d{2}|\b\d{1,2}\b|"
    r"\b(половин\w*|один|два|три|четыре|пять|шесть|семь|восемь|девять|десять|"
    r"одиннадцать|двенадцать|час\w*)\b",
    re.IGNORECASE,
)
_REMOVE_WORD = re.compile(
    r"\b(убер\w*|удал\w*|выключ\w*|отключ\w*|сним\w*)\b", re.IGNORECASE
)


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


def _ensure_grounded(intent: AssistantIntent, text: str) -> None:
    """Запретить модели добавлять поля, которых нет в тексте (R-15).

    Валидация значений ловит «25:00», но не выдуманное время на фразе без
    времени. Проверка опоры на текст превращает такую выдумку в отказ, а не в
    запись, которую пользователь не просил.
    """
    if intent.action is AssistantAction.CREATE:
        if intent.type is None or not _TYPE_WORDS[intent.type].search(text):
            raise IntentError("В тексте не назван тип тренировки")
        if not _WEEKDAY_WORD.search(text) and not _EVERY_DAY.search(text):
            raise IntentError("В тексте не названы дни недели")
        if not _TIME_WORD.search(text):
            raise IntentError("В тексте нет точного времени")
    elif intent.action is AssistantAction.OFF:
        if not _REMOVE_WORD.search(text):
            raise IntentError("В тексте нет просьбы убрать тренировку")


def parse_intent(raw_json: str, text: str | None = None) -> AssistantIntent:
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
            # «Каждый день» + точное время — это все семь дней; без явных дней
            # и без этой фразы create неполон (R-14, сценарий из корпуса).
            if text is not None and _EVERY_DAY.search(text):
                ar_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            else:
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

    result = AssistantIntent(
        action=action,
        type=wtype,
        days=days,
        time=time,
        workout_id=workout_id,
        reason=str(ar_reason) if ar_reason is not None else "",
    )
    if text is not None:
        _ensure_grounded(result, text)
    return result


_SYSTEM_PROMPT = """Ты переводишь фразу владельца про недельное расписание тренировок в JSON.
Отвечай только JSON-объектом, без пояснений и без markdown.

Поля:
- action: "create" | "list" | "off" | "none"
- type: "strength" | "cardio" | "mobility" (мобильность, растяжка) | null
- days: массив дней из ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"] или null
- time: "ЧЧ:ММ" в 24-часовом формате или null
- workout_id: целое число или null
- reason: короткое объяснение по-русски

Как выбирать action:
- create — в тексте есть тип, дни недели и точное время.
- list — просьба показать расписание.
- off — просьба убрать тренировку. workout_id бери из текущего расписания:
  «вторая тренировка» — это строка с номером 2 в списке, а не второй день.
- none — всё остальное.

Проверка перед create, все четыре пункта обязательны:
1. Тип назван словом из списка? Нет — none.
2. Дни недели названы? Нет — none.
3. Время точное, с часами и минутами? Нет — none.
4. Тип, дни и время взяты из текста, а не из расписания и не «по смыслу»?
   Нет — none. Расписание нужно только для off: не копируй из него поля.

Что всегда none:
- разовый перенос;
- замена одной тренировки другой;
- самочувствие и общие вопросы;
- повторение, невыразимое днями недели: «по чётным дням», «раз в две недели»;
- время без минут: «утром», «вечером», «днём»;
- несколько действий в одной фразе — верни только первое.

Время:
- только 24 часа: «в восемь утра» → "08:00", «в семь вечера» → "19:00";
- «половина седьмого вечера» → "18:30";
- «каждый день» или «ежедневно» вместе с точным временем — все семь дней;
- слово «утром» или «вечером» рядом с точным временем не мешает create.

При сомнении всегда возвращай none. Ложное действие хуже отказа."""

EMPTY_SCHEDULE_HINT = "Расписание пусто."


def build_system_prompt() -> str:
    """System-промпт: контракт полей, правило отказа и пример-якорь времени."""
    return _SYSTEM_PROMPT


def build_user_prompt(text: str, schedule_hint: str) -> str:
    """User-промпт: текст сообщения и read-only снапшот расписания (D4)."""
    snapshot = schedule_hint.strip() or EMPTY_SCHEDULE_HINT
    return f"Текущее расписание:\n{snapshot}\n\nСообщение владельца:\n{text.strip()}"