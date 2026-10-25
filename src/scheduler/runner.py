"""Запуск тика в APScheduler.

Расписание живёт в БД, а не в памяти планировщика (design.md D2), поэтому
здесь достаточно одного интервального job: после рестарта он просто продолжит
спрашивать базу, и будущие напоминания восстановятся сами (R-10).
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.scheduler.tick import SchedulerTick

JOB_ID = "reminder-tick"
DEFAULT_INTERVAL_SECONDS = 30


def build_scheduler(
    tick: SchedulerTick,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> AsyncIOScheduler:
    """Планировщик с одним интервальным job. Ещё не запущен."""
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
    return scheduler
