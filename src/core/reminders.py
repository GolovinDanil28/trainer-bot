"""Доставка: статусы напоминания и порт отправителя.

Здесь только контракт. Как именно сообщение попадает в Telegram, домен не
знает — благодаря этому доставку можно проверить без сети и без бота.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ReminderStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    ANSWERED = "answered"
    MISSED = "missed"


class CheckinAction(StrEnum):
    """Что можно нажать под напоминанием."""

    DONE = "done"
    SKIP = "skip"


class CheckinStatus(StrEnum):
    """Что хранится в журнале.

    Отличается от `CheckinAction` намеренно: в `callback_data` короткое `skip`
    (design.md D4), а в истории — `skipped` (design.md D3). Это словари разных
    слоёв, и путать их дороже, чем держать маппинг в одном месте.
    """

    DONE = "done"
    SKIPPED = "skipped"


CHECKIN_STATUS: dict[CheckinAction, CheckinStatus] = {
    CheckinAction.DONE: CheckinStatus.DONE,
    CheckinAction.SKIP: CheckinStatus.SKIPPED,
}
CHECKIN_STATUS_LABELS: dict[CheckinStatus, str] = {
    CheckinStatus.DONE: "сделал",
    CheckinStatus.SKIPPED: "пропустил",
}

# Подписи на кнопках. В callback_data всегда английские значения — подпись
# можно менять без миграции данных.
CHECKIN_LABELS: dict[CheckinAction, str] = {
    action: CHECKIN_STATUS_LABELS[CHECKIN_STATUS[action]] for action in CheckinAction
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
    workout_type: str
    duration_min: int
    scheduled_at_utc: datetime


class ReminderSender(Protocol):
    """Транспорт: единственное место, где живёт сеть."""

    async def send(self, message: ReminderMessage) -> None:
        """Отправить напоминание. При ошибке доставки — бросить исключение."""
        ...
