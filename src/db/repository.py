"""Репозиторий: операции с базой в терминах домена.

Функции принимают существующую сессию и ничего не знают ни про Telegram,
ни про текст сообщений. Транзакцией управляют они же: одна операция —
один commit.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, literal, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.history import HistoryEntry
from src.core.plan import (
    DayPlan,
    PlanSnapshotView,
    SnapshotItem,
    WeekReminder,
)
from src.core.reminders import (
    CHECKIN_STATUS,
    CheckinAction,
    CheckinStatus,
    ReminderMessage,
    ReminderStatus,
)
from src.core.schedule import Weekday, WorkoutDraft, WorkoutView
from src.core.wellness import WellnessEntry
from src.db.context import current_user_id
from src.db.models import (
    Checkin,
    PlanSnapshot,
    PlanSnapshotItem,
    Reminder,
    Schedule,
    WeeklyReport,
    Wellness,
    Workout,
)


def _owner(user_id: int | None) -> int:
    """Явный владелец или текущий из контекста (R-42)."""
    return current_user_id() if user_id is None else user_id


def create_workout(
    session: Session, draft: WorkoutDraft, user_id: int | None = None
) -> WorkoutView:
    """Создать тренировку и все записи расписания одной транзакцией."""
    workout = Workout(
        user_id=_owner(user_id),
        workout_type=str(draft.workout_type),
        duration_min=draft.duration_min,
        distance_km=draft.distance_km,
        date_once=draft.date_once,
        time_once=draft.time_once.strftime("%H:%M") if draft.time_once else None,
    )
    workout.schedules = [
        Schedule(weekday=int(day), time_of_day=draft.time_of_day.strftime("%H:%M"))
        for day in draft.weekdays
    ]

    session.add(workout)
    session.commit()

    return to_view(workout)


def list_active(
    session: Session, today: date | None = None, user_id: int | None = None
) -> list[WorkoutView]:
    """Активные тренировки, стабильно отсортированные по id.

    `today` скрывает прошедшие разовые (R-35): тренировка, которую уже не
    провести, не должна висеть в расписании. Недельные не фильтруются.
    """
    uid = _owner(user_id)
    workouts = session.scalars(
        select(Workout)
        .where(Workout.is_active.is_(True), Workout.user_id == uid)
        .order_by(Workout.id)
    ).all()
    views = [to_view(workout) for workout in workouts]
    if today is None:
        return views
    return [
        view
        for view in views
        if view.date_once is None or view.date_once >= today
    ]


def deactivate(session: Session, workout_id: int, user_id: int | None = None) -> bool:
    """Выключить тренировку. Возвращает False, если такой активной нет.

    Вместе с тренировкой отменяются её ещё не отправленные напоминания (R-03,
    задача 4.6): иначе `pending`, занятый до `/off`, всё равно уйдёт.
    Отправленные и отвеченные не трогаем — это история.
    """
    workout = session.scalars(
        select(Workout).where(
            Workout.id == workout_id,
            Workout.is_active.is_(True),
            Workout.user_id == _owner(user_id),
        )
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


def claim_reminder(
    session: Session,
    workout_id: int,
    scheduled_at: datetime,
    user_id: int | None = None,
) -> int | None:
    """Занять момент напоминания. Возвращает id, если занял именно этот вызов.

    `INSERT … ON CONFLICT DO NOTHING` вместо «проверить и вставить»: проверка
    с последующей вставкой оставляет окно, в которое второй тик влезает и
    отправляет то же напоминание второй раз.
    """
    # Чужую тренировку занять нельзя: проверка до вставки, потому что ключ
    # уникальности не знает про владельца.
    owner = session.scalar(
        select(Workout.id).where(
            Workout.id == workout_id, Workout.user_id == _owner(user_id)
        )
    )
    if owner is None:
        return None
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


def reminder_message(
    session: Session, reminder_id: int, user_id: int | None = None
) -> ReminderMessage | None:
    """Собрать всё нужное для отправки: напоминание + его тренировка.

    Возвращает `None`, если строки нет: между захватом момента и отправкой
    тренировку могли удалить, и падать из-за этого незачем.
    """
    row = session.execute(
        select(Reminder, Workout)
        .join(Workout, Reminder.workout_id == Workout.id)
        .where(Reminder.id == reminder_id, Workout.user_id == _owner(user_id))
    ).one_or_none()
    if row is None:
        return None

    reminder, workout = row
    workout_type = workout.workout_type
    # В v1 у тренировки одна длительность, поэтому она берётся из workout,
    # а не из расписания: `/new` не спрашивает разное время для разных дней.
    return ReminderMessage(
        reminder_id=reminder.id,
        workout_id=workout.id,
        workout_type=workout_type,
        duration_min=workout.duration_min,
        scheduled_at_utc=_as_utc(reminder.scheduled_at),
        distance_km=workout.distance_km,
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
    session: Session,
    reminder_id: int,
    action: CheckinAction,
    user_id: int | None = None,
) -> CheckinOutcome | None:
    """Записать отметку. Возвращает `None`, если напоминания нет.

    Первая отметка выигрывает: вторая не перезаписывает журнал (R-12). Гонку
    двух одновременных нажатий закрывает UNIQUE на `checkin.reminder_id`,
    а `IntegrityError` здесь — ожидаемый исход, а не ошибка.
    """
    reminder = session.scalar(
        select(Reminder)
        .join(Workout, Reminder.workout_id == Workout.id)
        .where(Reminder.id == reminder_id, Workout.user_id == _owner(user_id))
    )
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


def recent_history(
    session: Session, limit: int = 5, user_id: int | None = None
) -> list[HistoryEntry]:
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
            ),
            Workout.user_id == _owner(user_id),
        )
        .order_by(Reminder.scheduled_at.desc(), Reminder.id.desc())
        .limit(limit)
    ).all()

    return [
        HistoryEntry(
            scheduled_at_utc=_as_utc(reminder.scheduled_at),
            workout_type=workout.workout_type,
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


def active_schedule_rows(
    session: Session, user_id: int | None = None
) -> list[tuple[int, int, str]]:
    """Строки расписания, которые ещё действуют: `(workout_id, weekday, "HH:MM")`.

    Фильтр по `is_active` на двух таблицах сразу: отключённая тренировка не
    должна напоминать, даже если её строки расписания случайно остались
    активными.
    """
    rows = session.execute(
        select(Schedule.workout_id, Schedule.weekday, Schedule.time_of_day)
        .join(Workout, Schedule.workout_id == Workout.id)
        .where(
            Schedule.is_active.is_(True),
            Workout.is_active.is_(True),
            Workout.user_id == _owner(user_id),
        )
        .order_by(Schedule.workout_id, Schedule.weekday, Schedule.time_of_day)
    ).all()
    return [(row.workout_id, row.weekday, row.time_of_day) for row in rows]


def active_once_rows(
    session: Session, user_id: int | None = None
) -> list[tuple[int, date, str]]:
    """Разовые тренировки для тика: `(workout_id, дата, "HH:MM")` (R-35)."""
    rows = session.execute(
        select(Workout.id, Workout.date_once, Workout.time_once).where(
            Workout.is_active.is_(True),
            Workout.date_once.is_not(None),
            Workout.time_once.is_not(None),
            Workout.user_id == _owner(user_id),
        )
    )
    return [(row.id, row.date_once, row.time_once) for row in rows]


def workouts_for_day(
    session: Session, day: date, user_id: int | None = None
) -> list[WorkoutView]:
    """Тренировки на календарный день: недельные + разовая на эту дату (R-35)."""
    uid = _owner(user_id)
    weekly = workouts_for_weekday(session, Weekday(day.weekday()), uid)
    once = session.scalars(
        select(Workout).where(
            Workout.is_active.is_(True),
            Workout.date_once == day,
            Workout.user_id == uid,
        )
    ).all()
    # Порядок — по id, как у всех списков: план не должен зависеть от того,
    # в каком порядке база вернула строки.
    views = {view.id: view for view in weekly}
    views.update({view.id: to_view(view) for view in once})
    return [views[key] for key in sorted(views)]


def workouts_for_weekday(
    session: Session, weekday: Weekday, user_id: int | None = None
) -> list[WorkoutView]:
    """Активные тренировки, у которых этот день недели есть в расписании.

    Чтение для плана (R-24): `distinct` нужен, потому что теоретически у одной
    тренировки может быть две строки на один день; в плане она должна быть одна.
    """
    workouts = session.scalars(
        select(Workout)
        .distinct()
        .join(Schedule, Schedule.workout_id == Workout.id)
        .where(
            Workout.is_active.is_(True),
            Schedule.is_active.is_(True),
            Schedule.weekday == int(weekday),
            Workout.user_id == _owner(user_id),
        )
        .order_by(Workout.id)
    ).all()
    return [to_view(workout) for workout in workouts]


def claim_wellness_prompt(
    session: Session, day: date, now_utc: datetime, user_id: int | None = None
) -> bool:
    """Занять день под вечерний вопрос. `True` — если заняли именно мы (R-19).

    Тот же приём, что у `claim_reminder`: проверка «а не отправляли ли» перед
    вставкой оставила бы окно, в которое второй тик влезает и отправляет второй
    вопрос. `ON CONFLICT DO NOTHING` закрывает окно базой.
    """
    uid = _owner(user_id)
    statement = (
        sqlite_insert(Wellness)
        .values(
            user_id=uid, day=day.isoformat(), prompted_at=_naive_utc(now_utc)
        )
        .on_conflict_do_nothing(index_elements=["user_id", "day"])
        .returning(Wellness.id)
    )
    wellness_id = session.scalar(statement)
    session.commit()
    return wellness_id is not None


def wellness_for_day(
    session: Session, day: date, user_id: int | None = None
) -> WellnessEntry | None:
    """Самочувствие за день или `None`, если дня в базе нет."""
    row = session.scalar(
        select(Wellness).where(
            Wellness.day == day.isoformat(), Wellness.user_id == _owner(user_id)
        )
    )
    return None if row is None else _wellness_entry(row)


def release_wellness_prompt(
    session: Session, day: date, user_id: int | None = None
) -> bool:
    """Освободить день, если вопрос не удалось отправить.

    Удаляем только незаполненную строку: оценку, поставленную другим путём,
    трогать нельзя. Возвращает `True`, если что-то освободили.
    """
    result = session.execute(
        delete(Wellness).where(
            Wellness.day == day.isoformat(),
            Wellness.user_id == _owner(user_id),
            Wellness.score.is_(None),
        )
    )
    session.commit()
    return result.rowcount > 0


def save_wellness(
    session: Session,
    day: date,
    score: int,
    note: str | None,
    now_utc: datetime,
    user_id: int | None = None,
) -> WellnessEntry:
    """Записать оценку за день. Существующая оценка заменяется (R-20, R-21).

    `note=None` означает «заметку не меняли», а не «сотри»: команда
    `/wellness 5` без текста не должна стирать фразу, сказанную утром.
    Поэтому не «select → insert», а upsert: два одновременных сохранения
    не должны ронять друг друга `IntegrityError` (та же причина, что у
    `claim_wellness_prompt`).
    """
    moment = _naive_utc(now_utc)
    uid = _owner(user_id)
    statement = (
        sqlite_insert(Wellness)
        .values(
            user_id=uid,
            day=day.isoformat(),
            score=score,
            note=note,
            answered_at=moment,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "day"],
            set_={
                "score": score,
                "answered_at": moment,
                # NULL в note — «не меняли»: сохраняем прежнюю заметку.
                "note": func.coalesce(literal(note), Wellness.note),
            },
        )
    )
    session.execute(statement)
    session.commit()

    row = session.scalar(
        select(Wellness).where(
            Wellness.day == day.isoformat(), Wellness.user_id == uid
        )
    )
    if row is None:  # pragma: no cover — вставка выше только что создала строку
        raise RuntimeError(f"строка самочувствия за {day} не создалась")
    return _wellness_entry(row)


def _wellness_entry(row: Wellness) -> WellnessEntry:
    return WellnessEntry(
        day=date.fromisoformat(row.day),
        score=row.score,
        note=row.note,
    )


def cleanup_old_reminders(
    session: Session,
    user_id: int | None = None,
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
    # Владелец фильтруется подзапросом: у напоминания его нет, он у тренировки.
    own = select(Workout.id).where(Workout.user_id == _owner(user_id))
    sent_stale = session.execute(
        update(Reminder)
        .where(
            Reminder.status == ReminderStatus.SENT.value,
            Reminder.sent_at.is_not(None),
            Reminder.sent_at < _naive_utc(sent_before),
            Reminder.workout_id.in_(own),
        )
        .values(status=ReminderStatus.MISSED.value)
    )
    pending_stale = session.execute(
        update(Reminder)
        .where(
            Reminder.status == ReminderStatus.PENDING.value,
            Reminder.created_at < _naive_utc(pending_before),
            Reminder.workout_id.in_(own),
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
    # У разовой тренировки строк расписания нет: время живёт в `time_once`.
    # У недельной в v1 одно время на все дни (его задаёт `/new`), поэтому
    # достаточно любой строки; порядок в БД на результат не влияет.
    raw_moment = active[0].time_of_day if active else workout.time_once
    moment = time.fromisoformat(raw_moment) if raw_moment else time(0, 0)
    return WorkoutView(
        id=workout.id,
        workout_type=workout.workout_type,
        weekdays=days,
        time_of_day=moment,
        duration_min=workout.duration_min,
        distance_km=workout.distance_km,
        date_once=workout.date_once,
        time_once=time.fromisoformat(workout.time_once) if workout.time_once else None,
    )


def save_plan_snapshot(
    session: Session, plan_date: date, plan: DayPlan, user_id: int | None = None
) -> None:
    """Сохранить показанный план на дату (R-36).

    Один снимок на дату: старый удаляется целиком (элементы — каскадом), новый
    пишется на его место. Так повторный показ заменяет, а не раздваивает.
    """
    # Upsert, а не «select → delete → insert»: два одновременных показа плана
    # (двойной тап, будущий concurrent_updates) иначе дали бы IntegrityError на
    # UNIQUE(plan_date). Остальной репозиторий держится того же приёма.
    uid = _owner(user_id)
    session.execute(
        sqlite_insert(PlanSnapshot)
        .values(
            user_id=uid, plan_date=plan_date, score=plan.score, is_rest=plan.is_rest
        )
        .on_conflict_do_update(
            index_elements=["user_id", "plan_date"],
            set_={"score": plan.score, "is_rest": plan.is_rest},
        )
    )
    snapshot = session.scalar(
        select(PlanSnapshot).where(
            PlanSnapshot.plan_date == plan_date, PlanSnapshot.user_id == uid
        )
    )
    if snapshot is None:  # pragma: no cover — upsert только что её создал
        raise RuntimeError("снимок плана не найден после upsert")
    session.execute(
        delete(PlanSnapshotItem).where(
            PlanSnapshotItem.snapshot_id == snapshot.id
        )
    )
    session.add_all(
        [
            PlanSnapshotItem(
                snapshot_id=snapshot.id,
                workout_type=str(item.workout_type),
                time_of_day=item.time_of_day.strftime("%H:%M"),
                duration_min=item.duration_min,
                workout_id=item.workout_id,
            )
            for item in plan.items
        ]
    )
    session.commit()


def week_snapshots(
    session: Session, start_day: date, end_day: date, user_id: int | None = None
) -> list[PlanSnapshotView]:
    """Снимки планов за диапазон дат в удобном для разбора виде (R-37)."""
    rows = session.scalars(
        select(PlanSnapshot)
        .where(
            PlanSnapshot.plan_date.between(start_day, end_day),
            PlanSnapshot.user_id == _owner(user_id),
        )
        .order_by(PlanSnapshot.plan_date)
    ).all()
    return [
        PlanSnapshotView(
            plan_date=row.plan_date,
            score=row.score,
            is_rest=row.is_rest,
            items=tuple(
                SnapshotItem(
                    workout_type=item.workout_type,
                    time_of_day=time.fromisoformat(item.time_of_day),
                    duration_min=item.duration_min,
                    workout_id=item.workout_id,
                )
                for item in row.items
            ),
        )
        for row in rows
    ]


def week_reminders(
    session: Session, start_utc: datetime, end_utc: datetime, user_id: int | None = None
) -> list[WeekReminder]:
    """Напоминания за интервал (UTC) вместе с отметками — для разбора недели."""
    rows = session.execute(
        select(Reminder, Workout, Checkin)
        .join(Workout, Reminder.workout_id == Workout.id)
        .outerjoin(Checkin, Checkin.reminder_id == Reminder.id)
        .where(Reminder.scheduled_at >= _naive_utc(start_utc))
        .where(Reminder.scheduled_at < _naive_utc(end_utc))
        .where(Workout.user_id == _owner(user_id))
    )
    return [
        WeekReminder(
            workout_id=reminder.workout_id,
            scheduled_at_utc=_as_utc(reminder.scheduled_at),
            status=reminder.status,
            checkin=checkin.status if checkin is not None else None,
        )
        for reminder, _workout, checkin in rows
    ]


def wellness_range(
    session: Session, start_day: date, end_day: date, user_id: int | None = None
) -> list[WellnessEntry]:
    """Самочувствие за диапазон календарных дней (R-37)."""
    rows = session.scalars(
        select(Wellness)
        .where(Wellness.day >= start_day.isoformat())
        .where(Wellness.day <= end_day.isoformat())
        .where(Wellness.user_id == _owner(user_id))
        .order_by(Wellness.day)
    )
    return [_wellness_entry(row) for row in rows]


# Сколько живёт claim без доставки, прежде чем его перехватят снова. Тот же
# смысл, что у «зависшего» pending напоминания: крах процесса не должен съедать
# неделю.
WEEKLY_REPORT_STUCK_WINDOW = timedelta(minutes=10)


def claim_weekly_report(
    session: Session,
    week_start: date,
    now_utc: datetime,
    stale_after: timedelta = WEEKLY_REPORT_STUCK_WINDOW,
    user_id: int | None = None,
) -> bool:
    """Взять право на разбор недели. `False` — отправлен или уже в работе (R-40).

    Три состояния строки: нет строки — право свободно; `sent_at` заполнен —
    отправлено; `sent_at` пуст и `claimed_at` свежий — кто-то уже пытается;
    `sent_at` пуст, но claim протух — процесс умер, право перехватывается.
    """
    uid = _owner(user_id)
    result = session.execute(
        sqlite_insert(WeeklyReport)
        .values(
            user_id=uid, week_start=week_start, claimed_at=_naive_utc(now_utc)
        )
        .on_conflict_do_nothing(index_elements=["user_id", "week_start"])
    )
    session.commit()
    if result.rowcount == 1:
        return True

    # Перехват — одним UPDATE с условием, а не «прочитали и записали»:
    # иначе два процесса, увидевшие один протухший claim, оба решили бы, что
    # право их. claimed_at = NULL (старая база) считается протухшим.
    cutoff = _naive_utc(now_utc - stale_after)
    taken = session.execute(
        update(WeeklyReport)
        .where(
            WeeklyReport.week_start == week_start,
            WeeklyReport.user_id == uid,
            WeeklyReport.sent_at.is_(None),
            or_(
                WeeklyReport.claimed_at.is_(None),
                WeeklyReport.claimed_at <= cutoff,
            ),
        )
        .values(claimed_at=_naive_utc(now_utc))
    )
    session.commit()
    return taken.rowcount == 1


def mark_weekly_report_sent(
    session: Session, week_start: date, now_utc: datetime, user_id: int | None = None
) -> None:
    """Отметить доставку: с этого момента claim не перехватывается (R-40)."""
    session.execute(
        update(WeeklyReport)
        .where(
            WeeklyReport.week_start == week_start,
            WeeklyReport.user_id == _owner(user_id),
        )
        .values(sent_at=_naive_utc(now_utc))
    )
    session.commit()


def release_weekly_report(
    session: Session, week_start: date, user_id: int | None = None
) -> bool:
    """Вернуть право, если отправка сорвалась; отправленное не трогаем (R-40)."""
    result = session.execute(
        delete(WeeklyReport).where(
            WeeklyReport.week_start == week_start,
            WeeklyReport.user_id == _owner(user_id),
            WeeklyReport.sent_at.is_(None),
        )
    )
    session.commit()
    return result.rowcount > 0
