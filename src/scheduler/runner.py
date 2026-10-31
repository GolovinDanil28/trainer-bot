"""Запуск тика в APScheduler.

Расписание живёт в БД, а не в памяти планировщика (design.md D2), поэтому
здесь достаточно интервальных job: после рестарта они просто продолжат
спрашивать базу, и будущие события восстановятся сами (R-10, R-19).
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.scheduler.tick import SchedulerTick
from src.scheduler.weekly_tick import WeeklyTick
from src.scheduler.wellness_tick import WellnessTick

JOB_ID = "reminder-tick"
WELLNESS_JOB_ID = "wellness-tick"
WEEKLY_JOB_ID = "weekly-tick"
DEFAULT_INTERVAL_SECONDS = 30


def build_scheduler(
    tick: SchedulerTick,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    wellness_tick: WellnessTick | None = None,
    weekly_tick: WeeklyTick | None = None,
) -> AsyncIOScheduler:
    """Планировщик с интервальными job. Ещё не запущен."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        tick.run_now,
        trigger="interval",
        seconds=interval_seconds,
        id=JOB_ID,
        # Проход не должен накладываться сам на себя: если отправка затянулась,
        # следующий тик подождёт, а не начнёт второй параллельный проход.
        max_instances=1,
        coalesce=True,
    )
    if wellness_tick is not None:
        # Отдельный job, а не ветка в тике напоминаний: у вопроса свой порог
        # («время ≥ 21:00») и своя причина падать (Telegram), а общий job
        # заставил бы два независимых сценария делить один `max_instances`.
        scheduler.add_job(
            wellness_tick.run_now,
            trigger="interval",
            seconds=interval_seconds,
            id=WELLNESS_JOB_ID,
            max_instances=1,
            coalesce=True,
        )
    if weekly_tick is not None:
        # Свой job и свой порог («воскресенье, время ≥ 20:00»): общий job
        # заставил бы недельный разбор делить `max_instances` с напоминаниями.
        scheduler.add_job(
            weekly_tick.run_now,
            trigger="interval",
            seconds=interval_seconds,
            id=WEEKLY_JOB_ID,
            max_instances=1,
            coalesce=True,
        )
    return scheduler
