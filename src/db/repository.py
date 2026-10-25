"""Репозиторий: операции с базой в терминах домена.

Функции принимают существующую сессию и ничего не знают ни про Telegram,
ни про текст сообщений. Транзакцией управляют они же: одна операция —
один commit.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.history import HistoryEntry
from src.core.reminders import (
    CHECKIN_STATUS,
    CheckinAction,
    CheckinStatus,
    ReminderMessage,
    ReminderStatus,
)
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType, WorkoutView
from src.db.models import Checkin, Reminder, Schedule, Workout


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
    """Выключить тренировку. Возвращает False, если такой активной нет.

    Вместе с тренировкой отменяются её ещё не отправленные напоминания (R-03,
    задача 4.6): иначе `pending`, занятый до `/off`, всё равно уйдёт.
    Отправленные и отвеченные не трогаем — это история.
    """
    workout = session.scalars(
        select(Workout).where(Workout.id == workout_id, Workout.is_active.is_(True))
    ).one_or_none()
    if workout is None:
        return False

    workout.is_active = False
    for row in workout.schedules:
        row.is_active = False
    _cancel_pending_reminders(session, workout_id)
    session.commit()
    return True


def _cancel_pending_reminders(session: Session, workout_id: int) -> int:
    """Удалить неотправленные напоминания тренировки, вернуть их количество.

    Удаляем, а не помечаем: `pending` — это «ещё не случилось», истории в нём
    нет, а освободившийся уникальный ключ не помешает, если тренировку
    когда-нибудь включат обратно тем же моментом.
    """
    result = session.execute(
        delete(Reminder).where(
            Reminder.workout_id == workout_id,
            Reminder.status == ReminderStatus.PENDING.value,
        )
    )
    return result.rowcount


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


@dataclass(frozen=True)
class CheckinOutcome:
    """Результат нажатия: что теперь в журнале и создали ли запись мы."""

    status: CheckinStatus
    created: bool


def record_checkin(
    session: Session, reminder_id: int, action: CheckinAction
) -> CheckinOutcome | None:
    """Записать отметку. Возвращает `None`, если напоминания нет.

    Первая отметка выигрывает: вторая не перезаписывает журнал (R-12). Гонку
    двух одновременных нажатий закрывает UNIQUE на `checkin.reminder_id`,
    а `IntegrityError` здесь — ожидаемый исход, а не ошибка.
    """
    reminder = session.get(Reminder, reminder_id)
    if reminder is None:
        return None

    existing = _find_checkin(session, reminder_id)
    if existing is not None:
        return CheckinOutcome(CheckinStatus(existing.status), created=False)

    session.add(Checkin(reminder_id=reminder_id, status=CHECKIN_STATUS[action].value))
    reminder.status = ReminderStatus.ANSWERED.value
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _find_checkin(session, reminder_id)
        if existing is None:  # pragma: no cover — значит, упало не на UNIQUE
            raise
        return CheckinOutcome(CheckinStatus(existing.status), created=False)

    return CheckinOutcome(CHECKIN_STATUS[action], created=True)


def _find_checkin(session: Session, reminder_id: int) -> Checkin | None:
    return session.scalar(select(Checkin).where(Checkin.reminder_id == reminder_id))


def recent_history(session: Session, limit: int = 5) -> list[HistoryEntry]:
    """Последние записи журнала: отвеченные и «без ответа» (R-13).

    `pending` и `sent` не попадают: это ещё не результат, показывать нечего.
    Сортировка по моменту тренировки, а не по времени записи: журнал читается
    как «что было», а не «когда я нажал».
    """
    rows = session.execute(
        select(Reminder, Workout, Checkin)
        .join(Workout, Reminder.workout_id == Workout.id)
        .outerjoin(Checkin, Checkin.reminder_id == Reminder.id)
        .where(
            Reminder.status.in_(
                (ReminderStatus.ANSWERED.value, ReminderStatus.MISSED.value)
            )
        )
        .order_by(Reminder.scheduled_at.desc(), Reminder.id.desc())
        .limit(limit)
    ).all()

    return [
        HistoryEntry(
            scheduled_at_utc=_as_utc(reminder.scheduled_at),
            workout_type=WorkoutType(workout.workout_type),
            checkin=CheckinStatus(checkin.status) if checkin is not None else None,
        )
        for reminder, workout, checkin in rows
    ]


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


def active_schedule_rows(session: Session) -> list[tuple[int, int, str]]:
    """Строки расписания, которые ещё действуют: `(workout_id, weekday, "HH:MM")`.

    Фильтр по `is_active` на двух таблицах сразу: отключённая тренировка не
    должна напоминать, даже если её строки расписания случайно остались
    активными.
    """
    rows = session.execute(
        select(Schedule.workout_id, Schedule.weekday, Schedule.time_of_day)
        .join(Workout, Schedule.workout_id == Workout.id)
        .where(Schedule.is_active.is_(True), Workout.is_active.is_(True))
        .order_by(Schedule.workout_id, Schedule.weekday, Schedule.time_of_day)
    ).all()
    return [(row.workout_id, row.weekday, row.time_of_day) for row in rows]


def cleanup_old_reminders(
    session: Session,
    *,
    sent_before: datetime,
    pending_before: datetime,
) -> int:
    """Пометить `missed` то, что уже не будет отвечено (R-08).

    Границы приходят снаружи, потому что они зависят от таймзоны и настроек:
    `sent_before` — начало окна ожидания (по умолчанию начало местных суток),
    `pending_before` — порог «зависшего» `pending` (падение между вставкой
    и отправкой). Репозиторий не знает про таймзоны — ему дают готовый момент.

    `answered` не трогается никогда: отметка пользователя важнее времени.
    """
    sent_stale = session.execute(
        update(Reminder)
        .where(
            Reminder.status == ReminderStatus.SENT.value,
            Reminder.sent_at.is_not(None),
            Reminder.sent_at < _naive_utc(sent_before),
        )
        .values(status=ReminderStatus.MISSED.value)
    )
    pending_stale = session.execute(
        update(Reminder)
        .where(
            Reminder.status == ReminderStatus.PENDING.value,
            Reminder.created_at < _naive_utc(pending_before),
        )
        .values(status=ReminderStatus.MISSED.value)
    )
    session.commit()
    return sent_stale.rowcount + pending_stale.rowcount


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
