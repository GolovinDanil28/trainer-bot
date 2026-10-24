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
