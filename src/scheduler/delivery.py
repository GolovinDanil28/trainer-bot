"""Доставка напоминаний: отправка с повторами и фиксацией исхода.

Расписание решает «когда», этот модуль — «дошло ли». Разделение не формальное:
у них разные причины меняться и разные отказы. Сеть живёт за портом
`ReminderSender`, поэтому весь цикл проверяется `FakeSender`-ом, без Telegram.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from src.core.reminders import ReminderSender
from src.db import repository

# Три попытки — компромисс: две слишком мало (один таймаут — и напоминания нет),
# пять растягивают тик на минуты, если Telegram лежит целиком.
MAX_ATTEMPTS = 3


@dataclass
class DeliveryReport:
    """Итог прохода. Списки, а не счётчики: в тестах важно, что именно ушло."""

    sent: list[int] = field(default_factory=list)
    missed: list[int] = field(default_factory=list)
    lost: list[int] = field(default_factory=list)


class ReminderDelivery:
    """Отправить пачку уже занятых напоминаний.

    Занятые, а не найденные: право на отправку даёт `claim_reminder`, иначе
    после рестарта два процесса отправят одно и то же дважды.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        sender: ReminderSender,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._session_factory = session_factory
        self._sender = sender
        self._max_attempts = max_attempts

    async def deliver(self, reminder_ids: Sequence[int], now: datetime) -> DeliveryReport:
        """Обработать все id, не останавливаясь на неудачных."""
        report = DeliveryReport()
        for reminder_id in reminder_ids:
            outcome = await self._deliver_one(reminder_id, now)
            getattr(report, outcome).append(reminder_id)
        return report

    async def _deliver_one(self, reminder_id: int, now: datetime) -> str:
        with self._session_factory() as session:
            message = repository.reminder_message(session, reminder_id)
        if message is None:
            return "lost"

        attempts = 0
        while attempts < self._max_attempts:
            attempts += 1
            try:
                await self._sender.send(message)
            except Exception:
                # Ловим всё: транспорт падает чем угодно — таймаутом, обрывом,
                # 502 от Telegram. Одно упавшее напоминание не должно ронять тик,
                # иначе следующие тренировки молча остаются без напоминаний.
                continue

            with self._session_factory() as session:
                repository.mark_reminder_sent(session, reminder_id, attempts, now)
            return "sent"

        with self._session_factory() as session:
            repository.mark_reminder_missed(session, reminder_id, attempts)
        return "missed"
