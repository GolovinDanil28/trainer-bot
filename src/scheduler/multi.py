"""Тики по всем пользователям (R-06, R-19, R-40, R-44).

Один проход — обход активных пользователей: каждому свой чат, своя таймзона,
своё время вопроса и разбора. Ошибка у одного не останавливает остальных:
недоступный чат — не повод молчать всем.

Слой не знает про Telegram: отправители и рендер разбора приходят функциями
из `app.py` (composition root). Контекст пользователя (`acting_as`) выставляет
здесь, поэтому репозиторий фильтрует данные сам.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from src.core.reminders import ReminderSender
from src.core.users import UserProfile
from src.core.wellness import PromptSender
from src.db import repository
from src.db.context import acting_as
from src.scheduler.tick import SchedulerTick
from src.scheduler.weekly_tick import WeeklyTick
from src.scheduler.wellness_tick import WellnessTick

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobFactories:
    """Как собрать отправителей и текст для конкретного пользователя."""

    reminder_sender: Callable[[UserProfile], ReminderSender]
    wellness_sender: Callable[[UserProfile], PromptSender]
    report_sender: Callable[[UserProfile], Callable[[str], object]]
    render_weekly: Callable[[UserProfile, datetime], str]


class MultiUserJobs:
    """Три прохода планировщика, каждый — по всем активным пользователям."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        factories: JobFactories,
        miss_window: timedelta | None = None,
        catchup_window: timedelta | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._factories = factories
        self._miss_window = miss_window
        self._catchup_window = catchup_window

    def _users(self) -> list[UserProfile]:
        with self._session_factory() as session:
            return repository.active_users(session)

    async def run_reminders(self, now_utc: datetime) -> None:
        for user in self._users():
            zone = ZoneInfo(user.tz)
            tick = SchedulerTick(
                self._session_factory,
                self._factories.reminder_sender(user),
                zone,
                miss_window=self._miss_window,
                catchup_window=self._catchup_window,
            )
            await self._guarded(tick.run(now_utc), "напоминания", user)

    async def run_wellness(self, now_utc: datetime) -> None:
        for user in self._users():
            tick = WellnessTick(
                self._session_factory,
                self._factories.wellness_sender(user),
                ZoneInfo(user.tz),
                time.fromisoformat(user.wellness_time),
            )
            await self._guarded(tick.run(now_utc), "вопрос о самочувствии", user)

    async def run_weekly(self, now_utc: datetime) -> None:
        for user in self._users():
            tick = WeeklyTick(
                self._session_factory,
                self._factories.report_sender(user),
                ZoneInfo(user.tz),
                time.fromisoformat(user.week_review_time),
                # Текст собирается внутри `acting_as` (см. `_guarded`), иначе
                # репозиторий отдаст данные пользователя по умолчанию.
                render=lambda _start, _end, _until, user=user: (
                    self._factories.render_weekly(user, now_utc)
                ),
            )
            await self._guarded(tick.run(now_utc), "недельный разбор", user)

    async def _guarded(self, coro, what: str, user: UserProfile) -> None:  # noqa: ANN001
        """Выполнить проход пользователя, не уронив остальных."""
        with acting_as(user.id):
            try:
                await coro
            except Exception:
                logger.exception("%s: сбой у пользователя %s", what, user.id)


class ReminderJob:
    """Обёртка для APScheduler: `run_now` без аргументов."""

    def __init__(self, jobs: MultiUserJobs) -> None:
        self._jobs = jobs

    async def run_now(self) -> None:
        await self._jobs.run_reminders(datetime.now(UTC))


class WellnessJob:
    def __init__(self, jobs: MultiUserJobs) -> None:
        self._jobs = jobs

    async def run_now(self) -> None:
        await self._jobs.run_wellness(datetime.now(UTC))


class WeeklyJob:
    def __init__(self, jobs: MultiUserJobs) -> None:
        self._jobs = jobs

    async def run_now(self) -> None:
        await self._jobs.run_weekly(datetime.now(UTC))
