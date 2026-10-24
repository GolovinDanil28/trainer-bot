"""Репозиторий: операции с базой в терминах домена.

Функции принимают существующую сессию и ничего не знают ни про Telegram,
ни про текст сообщений. Транзакцией управляют они же: одна операция —
один commit.
"""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.core.reminders import ReminderMessage, ReminderStatus
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


def reminder_message(session: Session, reminder_id: int) -> ReminderMessage | None:
    """Собрать всё нужное для отправки: напоминание + его тренировка.

    Возвращает `None`, если строки нет: между захватом момента и отправкой
    тренировку могли удалить, и падать из-за этого незачем.
    """
    row = session.execute(
        select(Reminder, Workout)
        .join(Workout, Reminder.workout_id == Workout.id)
        .where(Reminder.id == reminder_id)
    ).one_or_none()
    if row is None:
        return None

    reminder, workout = row
    workout_type = WorkoutType(workout.workout_type)
    # В v1 у тренировки одна длительность, поэтому она берётся из workout,
    # а не из расписания: `/new` не спрашивает разное время для разных дней.
    return ReminderMessage(
        reminder_id=reminder.id,
        workout_id=workout.id,
        workout_type=workout_type,
        duration_min=workout.duration_min,
        scheduled_at_utc=_as_utc(reminder.scheduled_at),
    )


def mark_reminder_sent(
    session: Session, reminder_id: int, attempts: int, sent_at: datetime
) -> None:
    """Зафиксировать успешную отправку. `attempts` — сколько раз пробовали."""
    _set_reminder_status(
        session,
        reminder_id,
        status=ReminderStatus.SENT,
        attempts=attempts,
        sent_at=_naive_utc(sent_at),
    )


def mark_reminder_missed(session: Session, reminder_id: int, attempts: int) -> None:
    """Отправить не удалось за отведённые попытки: момент потерян (R-09)."""
    _set_reminder_status(
        session, reminder_id, status=ReminderStatus.MISSED, attempts=attempts
    )


def _set_reminder_status(
    session: Session,
    reminder_id: int,
    *,
    status: ReminderStatus,
    attempts: int,
    sent_at: datetime | None = None,
) -> None:
    session.execute(
        update(Reminder)
        .where(Reminder.id == reminder_id)
        .values(status=status.value, attempts=attempts, sent_at=sent_at)
    )
    session.commit()


def cleanup_old_reminders(
    session: Session, now: datetime, sent_window: timedelta = timedelta(seconds=30)
) -> int:
    """Перевести старые `sent` и «зависшие` `pending` в `missed`.

    R-08: возвращает количество обновлённых строк.

    Логика:
    1. `sent` older than `sent_window` → `missed` (пользователь не откликнулся,
       можно отправлять повторно).
    2. `pending` older than `sent_window` → `missed` (никто не Claim'ом,
       можно очистить).
    3. `answered` не трогаем вообще.
    """

    cutoff = _naive_utc(now - sent_window)

    # 1. `sent`, чей sent_at старше окна → missed
    sent_stale = (
        update(Reminder)
        .where(
            Reminder.status == ReminderStatus.SENT.value,
            Reminder.sent_at is not None,
            Reminder.sent_at < cutoff,
        )
        .values(status=ReminderStatus.MISSED.value, attempts=0)
    )

    # 2. `pending`, чей created_at старше окна → missed (никто неClaim'ом)
    pending_stale = (
        update(Reminder)
        .where(
            Reminder.status == ReminderStatus.PENDING.value,
            Reminder.created_at is not None,
            Reminder.created_at < cutoff,
        )
        .values(status=ReminderStatus.MISSED.value, attempts=0)
    )

    result1 = session.execute(sent_stale)
    result2 = session.execute(pending_stale)
    session.commit()
    return result1.rowcount + result2.rowcount


def _naive_utc(moment: datetime) -> datetime:
    """В базу — только naive-UTC: SQLite таймзону всё равно потеряет молча."""
    if moment.tzinfo is None:
        raise ValueError("время должно быть timezone-aware: непонятно, чей это час")
    return moment.astimezone(UTC).replace(tzinfo=None)


def _as_utc(moment: datetime) -> datetime:
    """Из базы — aware-UTC: наружу не должно утекать naive-время."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


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
