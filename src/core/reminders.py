"""Доставка: статусы напоминания и порт отправителя.

Здесь только контракт. Как именно сообщение попадает в Telegram, домен не
знает — благодаря этому доставку можно проверить без сети и без бота.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from src.core.schedule import WorkoutType


class ReminderStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    ANSWERED = "answered"
    MISSED = "missed"


class CheckinAction(StrEnum):
    """Что можно нажать под напоминанием."""

    DONE = "done"
    SKIP = "skip"


# Подписи на кнопках. В callback_data всегда английские значения — подпись
# можно менять без миграции данных.
CHECKIN_LABELS: dict[CheckinAction, str] = {
    CheckinAction.DONE: "сделал",
    CheckinAction.SKIP: "пропустил",
}

CALLBACK_PREFIX = "ci"


class CallbackError(ValueError):
    """Некорректные данные кнопки. Пользователю показывать не нужно."""


def checkin_callback(reminder_id: int, action: CheckinAction) -> str:
    """`ci:<reminder_id>:<done|skip>` — данные кнопки под напоминанием.

    В кнопке лежит только id, а не тренировка и дата: id проверяется по базе,
    а подделанные данные из кнопки ничего не смогут изменить.
    """
    return f"{CALLBACK_PREFIX}:{reminder_id}:{action.value}"


def parse_checkin_callback(data: str) -> tuple[int, CheckinAction]:
    """Разобрать данные кнопки. Бросает `CallbackError` на мусор."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        raise CallbackError(f"ожидался формат {CALLBACK_PREFIX}:<id>:<action>, получено «{data}»")

    try:
        reminder_id = int(parts[1])
    except ValueError as exc:
        raise CallbackError(f"id напоминания должен быть числом, получено «{parts[1]}»") from exc

    try:
        action = CheckinAction(parts[2])
    except ValueError as exc:
        raise CallbackError(f"неизвестное действие «{parts[2]}»") from exc

    return reminder_id, action


@dataclass(frozen=True, slots=True)
class ReminderMessage:
    """Всё, что нужно, чтобы показать напоминание пользователю."""

    reminder_id: int
    workout_id: int
    workout_type: WorkoutType
    duration_min: int
    scheduled_at_utc: datetime


class ReminderSender(Protocol):
    """Транспорт: единственное место, где живёт сеть."""

    async def send(self, message: ReminderMessage) -> None:
        """Отправить напоминание. При ошибке доставки — бросить исключение."""
        ...
