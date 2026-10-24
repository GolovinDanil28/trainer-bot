"""Репозиторий: операции с базой в терминах домена.

Функции принимают существующую сессию и ничего не знают ни про Telegram,
ни про текст сообщений. Транзакцией управляют они же: одна операция —
один commit.
"""

from datetime import UTC, datetime, time

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.core.reminders import ReminderStatus
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType, WorkoutView
from src.db.models import Reminder, Schedule, Workout


def create_workout(session: Session, draft: WorkoutDraft) -> WorkoutView:
    """Создать тренировку и все записи расписания одной транзакцией."""
    workout = Workout(
        workout_type=draft.workout_type.value,
        duration_min=draft.duration_min,
    )
    workout.schedules = [
        Schedule(weekday=int(day), time_of_day=draft.time_of_day.strftime("%H:%M"))
        for day in draft.weekdays
    ]

    session.add(workout)
    session.commit()

    return to_view(workout)


def list_active(session: Session) -> list[WorkoutView]:
    """Активные тренировки, стабильно отсортированные по id."""
    workouts = session.scalars(
        select(Workout).where(Workout.is_active.is_(True)).order_by(Workout.id)
    ).all()
    return [to_view(workout) for workout in workouts]


def deactivate(session: Session, workout_id: int) -> bool:
    """Выключить тренировку. Возвращает False, если такой активной нет."""
    workout = session.scalars(
        select(Workout).where(Workout.id == workout_id, Workout.is_active.is_(True))
    ).one_or_none()
    if workout is None:
        return False

    workout.is_active = False
    for row in workout.schedules:
        row.is_active = False
    session.commit()
    return True


def claim_reminder(session: Session, workout_id: int, scheduled_at: datetime) -> int | None:
    """Занять момент напоминания. Возвращает id, если занял именно этот вызов.

    `INSERT … ON CONFLICT DO NOTHING` вместо «проверить и вставить»: проверка
    с последующей вставкой оставляет окно, в которое второй тик влезает и
    отправляет то же напоминание второй раз.
    """
    statement = (
        sqlite_insert(Reminder)
        .values(
            workout_id=workout_id,
            scheduled_at=_naive_utc(scheduled_at),
            status=ReminderStatus.PENDING.value,
        )
        .on_conflict_do_nothing(index_elements=["workout_id", "scheduled_at"])
        .returning(Reminder.id)
    )
    reminder_id = session.scalar(statement)
    session.commit()
    return reminder_id


def _naive_utc(moment: datetime) -> datetime:
    """В базу — только naive-UTC: SQLite таймзону всё равно потеряет молча."""
    if moment.tzinfo is None:
        raise ValueError("время должно быть timezone-aware: непонятно, чей это час")
    return moment.astimezone(UTC).replace(tzinfo=None)


def to_view(workout: Workout) -> WorkoutView:
    """Модель БД → структура домена для показа пользователю."""
    active = [row for row in workout.schedules if row.is_active]
    days = tuple(sorted(Weekday(row.weekday) for row in active))
    # В v1 у тренировки одно время на все дни (его задаёт `/new`), поэтому
    # берём минимальное строкой — результат не зависит от порядка строк в БД.
    moment = min(row.time_of_day for row in active)
    return WorkoutView(
        id=workout.id,
        workout_type=WorkoutType(workout.workout_type),
        weekdays=days,
        time_of_day=time.fromisoformat(moment),
        duration_min=workout.duration_min,
    )
