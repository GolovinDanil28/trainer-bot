"""Журнал выполнения `/history` (5.3, R-13).

Проверяем то, что видит человек: порядок, лимит в пять строк, перевод времени
в таймзону и честное «без ответа» вместо выдуманного результата.
"""

from datetime import UTC, datetime, time

from src.core.messages import EMPTY_HISTORY_HINT
from src.core.reminders import CheckinAction
from src.core.schedule import Weekday, WorkoutDraft, WorkoutType
from src.db import repository


def add_history(
    session_factory,
    *,
    moment: datetime,
    workout_type: WorkoutType,
    action: CheckinAction | None = None,
) -> int:
    """Довести напоминание до строки журнала.

    `action=None` — напоминание ушло и осталось без ответа (`missed`).
    """
    draft = WorkoutDraft(
        workout_type=workout_type,
        weekdays=(Weekday.MONDAY,),
        time_of_day=time(19, 30),
        duration_min=45,
    )
    with session_factory() as session:
        workout = repository.create_workout(session, draft)
        reminder_id = repository.claim_reminder(session, workout.id, moment)
        repository.mark_reminder_sent(session, reminder_id, 1, moment)
        if action is None:
            repository.mark_reminder_missed(session, reminder_id, attempts=3)
        else:
            repository.record_checkin(session, reminder_id, action)
    return reminder_id


def test_empty_history_hints_how_to_get_first_entry(service):
    assert service.show_history() == EMPTY_HISTORY_HINT
    assert "История пока пуста" in EMPTY_HISTORY_HINT


def test_history_shows_three_results_newest_first(service, session_factory):
    add_history(
        session_factory,
        moment=datetime(2026, 10, 24, 4, 15, tzinfo=UTC),
        workout_type=WorkoutType.MOBILITY,
    )
    add_history(
        session_factory,
        moment=datetime(2026, 10, 26, 14, 30, tzinfo=UTC),
        workout_type=WorkoutType.STRENGTH,
        action=CheckinAction.DONE,
    )
    add_history(
        session_factory,
        moment=datetime(2026, 10, 25, 6, 0, tzinfo=UTC),
        workout_type=WorkoutType.CARDIO,
        action=CheckinAction.SKIP,
    )

    # UTC+5: 14:30 UTC — это 19:30 у пользователя, дата не «съезжает».
    assert service.show_history() == (
        "Последние записи:\n"
        "26.10.2026 19:30 — силовая, сделал\n"
        "25.10.2026 11:00 — кардио, пропустил\n"
        "24.10.2026 09:15 — мобильность, без ответа"
    )


def test_history_keeps_only_five_latest(service, session_factory):
    for day in range(1, 7):
        add_history(
            session_factory,
            moment=datetime(2026, 10, day, 12, 0, tzinfo=UTC),
            workout_type=WorkoutType.CARDIO,
            action=CheckinAction.DONE,
        )

    lines = service.show_history().splitlines()
    assert len(lines) == 6  # заголовок + пять записей
    assert "06.10.2026" in lines[1]
    assert "01.10.2026" not in service.show_history()


def test_pending_and_sent_are_not_history_yet(service, session_factory):
    draft = WorkoutDraft(
        workout_type=WorkoutType.STRENGTH,
        weekdays=(Weekday.MONDAY,),
        time_of_day=time(19, 30),
        duration_min=45,
    )
    with session_factory() as session:
        workout = repository.create_workout(session, draft)
        # pending: тик ещё не отправлял — результата нет.
        repository.claim_reminder(session, workout.id, datetime(2026, 10, 24, tzinfo=UTC))
        # sent: отправили, но ответа пока нет и окно не закрыто.
        sent_id = repository.claim_reminder(
            session, workout.id, datetime(2026, 10, 25, tzinfo=UTC)
        )
        repository.mark_reminder_sent(
            session, sent_id, 1, datetime(2026, 10, 25, tzinfo=UTC)
        )

    add_history(
        session_factory,
        moment=datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
        workout_type=WorkoutType.STRENGTH,
        action=CheckinAction.DONE,
    )

    text = service.show_history()
    assert text.count("—") == 1
    assert "сделал" in text
