"""Журнал выполнения: то, что видит пользователь по `/history` (R-13).

Запись журнала — это не только `checkin`: напоминание без ответа тоже строка
истории, просто с результатом «без ответа».
"""

from dataclasses import dataclass
from datetime import datetime

from src.core.reminders import CheckinStatus
from src.core.schedule import WorkoutType


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """Одна строка журнала: когда была тренировка, какая и чем закончилась."""

    scheduled_at_utc: datetime
    workout_type: WorkoutType
    # None — напоминание ушло, но отметки нет: в тексте это «без ответа».
    checkin: CheckinStatus | None
