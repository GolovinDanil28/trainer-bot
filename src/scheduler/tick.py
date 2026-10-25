"""Один проход планировщика: найти моменты, отправить, подчистить.

Тик — тонкая склейка трёх уже проверенных частей: `timeplan` отвечает, какой
момент наступил, `repository` — кто владеет моментом, `ReminderDelivery` —
дошло ли сообщение. Здесь нет ни SQL-логики, ни форматирования текста.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from src.core.reminders import ReminderSender
from src.core.schedule import Weekday
from src.core.timeplan import DEFAULT_TICK_WINDOW, moment_in_window, waiting_cutoff
from src.db import repository
from src.scheduler.delivery import DeliveryReport, ReminderDelivery

# «Зависший» pending — это след падения между вставкой и отправкой. Пять минут
# с запасом больше любого таймаута Telegram, но меньше, чем интервал, за который
# пользователь успел бы заметить пропажу.
PENDING_STUCK_WINDOW = timedelta(minutes=5)


@dataclass
class TickResult:
    """Что сделал один проход. Возвращается, чтобы это можно было проверить."""

    moments: list[tuple[int, datetime]] = field(default_factory=list)
    claimed: list[int] = field(default_factory=list)
    delivery: DeliveryReport = field(default_factory=DeliveryReport)
    cleaned: int = 0


class SchedulerTick:
    """Интервальный тик: состояние читается из БД, поэтому рестарт ничего не теряет."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        sender: ReminderSender,
        zone: ZoneInfo,
        *,
        window: timedelta = DEFAULT_TICK_WINDOW,
        miss_window: timedelta | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._zone = zone
        self._window = window
        self._miss_window = miss_window
        self._delivery = ReminderDelivery(session_factory, sender)

    def due_moments(self, now_utc: datetime) -> list[tuple[int, datetime]]:
        """Моменты, попавшие в окно тика, вместе с id тренировок."""
        with self._session_factory() as session:
            rows = repository.active_schedule_rows(session)

        moments: list[tuple[int, datetime]] = []
        for workout_id, weekday, raw_time in rows:
            moment = moment_in_window(
                now_utc,
                self._zone,
                Weekday(weekday),
                time.fromisoformat(raw_time),
                self._window,
            )
            if moment is not None:
                moments.append((workout_id, moment))
        return moments

    async def run(self, now_utc: datetime) -> TickResult:
        """Один проход. `now_utc` передаётся явно: тесты не ждут реального времени."""
        moments = self.due_moments(now_utc)

        claimed: list[int] = []
        for workout_id, moment in moments:
            with self._session_factory() as session:
                reminder_id = repository.claim_reminder(session, workout_id, moment)
            # None значит «этот момент уже занят»: либо прошлый тик, либо
            # параллельный процесс. Отправлять второй раз нельзя (R-06).
            if reminder_id is not None:
                claimed.append(reminder_id)

        report = await self._delivery.deliver(claimed, now_utc)

        with self._session_factory() as session:
            cleaned = repository.cleanup_old_reminders(
                session,
                sent_before=waiting_cutoff(now_utc, self._zone, self._miss_window),
                pending_before=now_utc - PENDING_STUCK_WINDOW,
            )

        return TickResult(moments=moments, claimed=claimed, delivery=report, cleaned=cleaned)

    async def run_now(self) -> TickResult:
        """Проход по текущим часам — то, что вызывает APScheduler."""
        return await self.run(datetime.now(UTC))
