"""Недельный разбор и советы (R-37, R-38): окно недели, N из M, причины, правила."""

import asyncio
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY, REFUSAL_TEXT
from src.bot.handlers import cmd_week
from src.bot.service import WorkoutService
from src.bot.wellness_service import WellnessService
from src.core.messages import ADVICE_TEXTS, WEEK_SCHEDULE_FOOTER, format_weekly_review
from src.core.plan import (
    MissReason,
    PlanSnapshotView,
    ScheduleAdvice,
    SnapshotItem,
    WeekReminder,
    build_weekly_review,
    week_window,
)
from src.core.reminders import CheckinAction
from src.core.schedule import Weekday
from src.core.wellness import WellnessEntry
from src.db import repository
from src.db.models import Checkin, PlanSnapshot, Reminder, Schedule, Workout
from src.scheduler.tick import SchedulerTick
from tests.fakes import FakeBot, FakeSender, make_context, make_update

ZONE = ZoneInfo("UTC")
WEEK_START = date(2026, 10, 26)
WEEK_END = date(2026, 11, 1)


def _snapshot(day, items, score=4, is_rest=False):
    return PlanSnapshotView(
        plan_date=day,
        score=score,
        is_rest=is_rest,
        items=tuple(items),
    )


def _item(workout_id, at=time(8, 0), workout_type="бег"):
    return SnapshotItem(
        workout_type=workout_type,
        time_of_day=at,
        duration_min=70,
        workout_id=workout_id,
    )


def _reminder(workout_id, day, at=time(8, 0), status="answered", checkin=None):
    return WeekReminder(
        workout_id=workout_id,
        scheduled_at_utc=datetime.combine(day, at, tzinfo=UTC),
        status=status,
        checkin=checkin,
    )


# --- Окно недели (D3) -----------------------------------------------------


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 11, 2), (WEEK_START, WEEK_END)),  # понедельник
        (date(2026, 11, 5), (WEEK_START, WEEK_END)),  # четверг
        (date(2026, 11, 1), (date(2026, 10, 19), date(2026, 10, 25))),  # воскресенье
        (date(2027, 1, 1), (date(2026, 12, 21), date(2026, 12, 27))),  # граница года
    ],
)
def test_week_window(today, expected):
    assert week_window(today) == expected


# --- N из M и причины -----------------------------------------------------


def test_counts_done_from_reminders():
    snapshots = [
        _snapshot(WEEK_START, [_item(1), _item(2, time(19, 0))]),
        _snapshot(date(2026, 10, 28), [_item(3)]),
    ]
    reminders = [
        _reminder(1, WEEK_START, checkin="done"),
        _reminder(2, WEEK_START, time(19, 0), checkin="done"),
        _reminder(3, date(2026, 10, 28), checkin="skipped"),
    ]

    review = build_weekly_review(
        snapshots, reminders, {}, WEEK_START, WEEK_END, ZONE
    )

    assert (review.total, review.done) == (3, 2)
    assert [miss.reason for miss in review.misses] == [MissReason.SKIPPED]


def test_miss_reasons_are_facts():
    snapshots = [
        _snapshot(
            WEEK_START,
            [_item(1), _item(2), _item(3), _item(4, time(21, 0))],
        )
    ]
    reminders = [
        _reminder(1, WEEK_START, checkin="skipped"),
        _reminder(2, WEEK_START, status="missed"),
        _reminder(3, WEEK_START, status="sent", checkin=None),
        # для четвёртого элемента напоминания нет вовсе
    ]

    review = build_weekly_review(
        snapshots, reminders, {}, WEEK_START, WEEK_END, ZONE
    )

    assert [(m.workout_type, m.reason) for m in review.misses] == [
        ("бег", MissReason.SKIPPED),
        ("бег", MissReason.MISSED),
        ("бег", MissReason.NO_ANSWER),
        ("бег", MissReason.NO_REMINDER),
    ]


def test_wellness_accompanies_misses():
    snapshot = _snapshot(WEEK_START, [_item(1)])
    wellness = {
        WEEK_START: WellnessEntry(day=WEEK_START, score=2, note="не выспался")
    }

    review = build_weekly_review(
        [snapshot], [], wellness, WEEK_START, WEEK_END, ZONE
    )

    miss = review.misses[0]
    assert (miss.score, miss.note) == (2, "не выспался")


def test_rest_snapshot_counts_days_not_items():
    review = build_weekly_review(
        [_snapshot(WEEK_START, [], score=1, is_rest=True)], [], {},
        WEEK_START, WEEK_END, ZONE,
    )

    assert review.total == 0
    assert review.rest_days == 1
    assert review.has_snapshots is True


def test_no_snapshots_is_stated():
    review = build_weekly_review([], [], {}, WEEK_START, WEEK_END, ZONE)

    assert review.has_snapshots is False
    assert "Планов за эту неделю нет" in format_weekly_review(review)


# --- Советы (R-38) --------------------------------------------------------


def _advice(snapshots, reminders, wellness=None):
    return build_weekly_review(
        snapshots, reminders, wellness or {}, WEEK_START, WEEK_END, ZONE
    ).advice


def test_advice_add_when_everything_done():
    snapshots = [_snapshot(WEEK_START, [_item(i) for i in (1, 2, 3, 4)])]
    reminders = [_reminder(i, WEEK_START, checkin="done") for i in (1, 2, 3, 4)]

    assert _advice(snapshots, reminders) is ScheduleAdvice.ADD_WORKOUT


def test_advice_reduce_or_morning_on_low_wellness():
    snapshots = [_snapshot(WEEK_START, [_item(1), _item(2)])]
    wellness = {
        WEEK_START: WellnessEntry(day=WEEK_START, score=2, note=None),
    }

    assert _advice(snapshots, [], wellness) is ScheduleAdvice.REDUCE_OR_MORNING


def test_advice_reduce_when_most_missed():
    snapshots = [_snapshot(WEEK_START, [_item(i) for i in range(1, 7)])]
    reminders = [
        _reminder(1, WEEK_START, checkin="done"),
        _reminder(2, WEEK_START, checkin="done"),
    ]

    assert _advice(snapshots, reminders) is ScheduleAdvice.REDUCE


def test_advice_none_when_week_matches_load():
    snapshots = [_snapshot(WEEK_START, [_item(1), _item(2)])]
    reminders = [_reminder(1, WEEK_START, checkin="done")]

    assert _advice(snapshots, reminders) is ScheduleAdvice.NONE


def test_average_score_is_per_day_not_per_item():
    """День с четырьмя пропусками не должен весить втрое.

    По дням: (1 + 5) / 2 = 3.0 → правило про самочувствие не срабатывает.
    По элементам: (1*4 + 5) / 5 = 1.8 → сработало бы.
    """
    day_a, day_b = WEEK_START, date(2026, 10, 28)
    snapshots = [
        _snapshot(day_a, [_item(1), _item(2), _item(3), _item(4)]),
        _snapshot(day_b, [_item(5)]),
    ]
    wellness = {
        day_a: WellnessEntry(day=day_a, score=1, note=None),
        day_b: WellnessEntry(day=day_b, score=5, note=None),
    }

    review = build_weekly_review(
        snapshots, [], wellness, WEEK_START, WEEK_END, ZONE
    )

    assert review.advice is ScheduleAdvice.REDUCE


def test_low_wellness_rule_beats_reduce():
    # Оба правила подходят; приоритет — сначала «сократить или на утро».
    snapshots = [_snapshot(WEEK_START, [_item(i) for i in range(1, 6)])]
    reminders = [_reminder(1, WEEK_START, checkin="done")]
    wellness = {WEEK_START: WellnessEntry(day=WEEK_START, score=2, note=None)}

    assert _advice(snapshots, reminders, wellness) is ScheduleAdvice.REDUCE_OR_MORNING


# --- Текст разбора --------------------------------------------------------


def test_review_text_has_numbers_reasons_and_footer():
    snapshots = [_snapshot(WEEK_START, [_item(1), _item(2)])]
    reminders = [_reminder(1, WEEK_START, checkin="done")]
    wellness = {WEEK_START: WellnessEntry(day=WEEK_START, score=2, note="устал")}

    text = format_weekly_review(
        build_weekly_review(snapshots, reminders, wellness, WEEK_START, WEEK_END, ZONE)
    )

    assert "Сделано 1 из 2 тренировок" in text
    assert "Что мешало" in text
    assert "самочувствие 2/5 «устал»" in text
    assert "Расписание я не меняю" in text
    assert "/off" in text and "/new" in text


# --- /week через сервис и хендлер -----------------------------------------


def _prepare_week(session_factory):
    """Вечер понедельника → снимок на вторник → напоминание → отметка «сделал»."""
    monday = datetime(2026, 10, 26, 21, 0, tzinfo=UTC)
    wellness = WellnessService(session_factory, ZONE, clock=lambda: monday)
    workouts = WorkoutService(session_factory, ZONE, clock=lambda: monday)
    workouts.create_weekly("бег", (Weekday.TUESDAY,), time(8, 0), distance_km=7)
    with session_factory() as session:
        repository.save_wellness(session, monday.date(), 4, None, monday)

    # Показ плана — единственный путь, который пишет снимок.
    wellness.show_plan(monday)

    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE)
    asyncio.run(tick.run(datetime(2026, 10, 27, 8, 0, tzinfo=UTC)))
    workouts.record_checkin(sender.sent[0].reminder_id, CheckinAction.DONE)


def test_weekly_review_service_counts_past_week(session_factory):
    _prepare_week(session_factory)
    monday_next = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    service = WellnessService(session_factory, ZONE, clock=lambda: monday_next)

    text = service.weekly_review()

    assert "Сделано 1 из 1 тренировки" in text
    assert "Разбор недели 26.10–01.11" in text


def test_week_command_does_not_change_schedule(session_factory):
    _prepare_week(session_factory)
    monday_next = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    service = WellnessService(session_factory, ZONE, clock=lambda: monday_next)
    workouts = WorkoutService(session_factory, ZONE, clock=lambda: monday_next)

    before = workouts.show_schedule()
    service.weekly_review()

    assert workouts.show_schedule() == before
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 1


def test_week_handler_owner_and_stranger(session_factory):
    _prepare_week(session_factory)
    monday_next = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    wellness = WellnessService(session_factory, ZONE, clock=lambda: monday_next)

    owner = make_update("/week")
    context = make_context({"wellness_service": wellness, OWNER_KEY: 42})

    asyncio.run(cmd_week(owner, context))
    assert "Разбор недели" in owner.message.sent[0]

    stranger = make_update("/week", user_id=999)
    asyncio.run(cmd_week(stranger, context))
    assert stranger.message.sent == [REFUSAL_TEXT]


# --- Ветки текста, которые ловило ревью ------------------------------------


def test_rest_only_week_still_has_advice_and_footer():
    review = build_weekly_review(
        [_snapshot(WEEK_START, [], score=2, is_rest=True)], [], {},
        WEEK_START, WEEK_END, ZONE,
    )

    text = format_weekly_review(review)

    assert "советовал отдых 1 день" in text
    assert ADVICE_TEXTS[review.advice] in text
    assert WEEK_SCHEDULE_FOOTER in text


def test_empty_plan_week_is_not_called_rest():
    # Снимок без элементов и без отдыха: бот не советовал отдых — в расписании
    # просто нечего было планировать.
    empty = _snapshot(WEEK_START, [], score=4, is_rest=False)

    text = format_weekly_review(
        build_weekly_review([empty], [], {}, WEEK_START, WEEK_END, ZONE)
    )

    assert "отдых" not in text
    assert "ничего не стояло" in text
    assert WEEK_SCHEDULE_FOOTER in text


def test_no_snapshot_text_has_no_footer_promise():
    text = format_weekly_review(
        build_weekly_review([], [], {}, WEEK_START, WEEK_END, ZONE)
    )

    # Неделя без данных: совет и «расписание не меняю» были бы шумом.
    assert "Расписание я не меняю" not in text


@pytest.mark.parametrize("advice", list(ScheduleAdvice))
def test_every_advice_has_text(advice):
    assert advice in ADVICE_TEXTS
    assert ADVICE_TEXTS[advice]


def test_advice_texts_do_not_order_around():
    for text in ADVICE_TEXTS.values():
        assert "надо" not in text.lower()
        assert "должен" not in text.lower()
    assert "/off" in WEEK_SCHEDULE_FOOTER and "/new" in WEEK_SCHEDULE_FOOTER


def test_no_reminder_reason_is_visible_in_text():
    snapshot = _snapshot(WEEK_START, [_item(1)])

    text = format_weekly_review(
        build_weekly_review([snapshot], [], {}, WEEK_START, WEEK_END, ZONE)
    )

    assert "напоминания не было" in text


def test_single_missed_workout_does_not_advise_removal():
    # M = 1, N = 0: «убрать одну» звучало бы насмешкой (D5).
    snapshots = [_snapshot(WEEK_START, [_item(1)])]

    assert _advice(snapshots, []) is ScheduleAdvice.NONE


# --- Таймзона: неделя считается по локальным датам -------------------------


def test_week_membership_uses_users_timezone():
    zone = ZoneInfo("Asia/Yekaterinburg")  # UTC+5
    snapshot = _snapshot(date(2026, 10, 26), [_item(1), _item(2, time(1, 0))])
    reminders = [
        # 25.10T20:00Z = 26.10 01:00 локально → в неделе
        _reminder(2, date(2026, 10, 25), at=time(20, 0), checkin="done"),
        # 26.10T19:30Z = 27.10 00:30 локально → вне недели
        _reminder(1, date(2026, 10, 26), at=time(19, 30), checkin="done"),
    ]

    review = build_weekly_review(
        [snapshot], reminders, {}, WEEK_START, WEEK_END, zone
    )

    assert (review.total, review.done) == (2, 1)
    assert [miss.workout_type for miss in review.misses] == ["бег"]


# --- Вечерняя кнопка тоже пишет снимок ------------------------------------


def test_button_answer_writes_single_snapshot(session_factory):
    monday = datetime(2026, 10, 26, 21, 0, tzinfo=UTC)
    wellness = WellnessService(session_factory, ZONE, clock=lambda: monday)
    workouts = WorkoutService(session_factory, ZONE, clock=lambda: monday)
    workouts.create_weekly("бег", (Weekday.TUESDAY,), time(8, 0), distance_km=7)
    with session_factory() as session:
        repository.save_wellness(session, monday.date(), 4, None, monday)

    wellness.record_from_button(monday.date(), 4)
    wellness.record_from_button(monday.date(), 4)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PlanSnapshot)) == 1


# --- R-24: план и разбор не трогают расписание и напоминания ---------------


def _counts(session_factory):
    with session_factory() as session:
        return {
            "workouts": session.scalar(select(func.count()).select_from(Workout)),
            "schedules": session.scalar(select(func.count()).select_from(Schedule)),
            "reminders": session.scalar(select(func.count()).select_from(Reminder)),
            "checkins": session.scalar(select(func.count()).select_from(Checkin)),
        }


def test_plan_and_week_do_not_touch_schedule_or_reminders(session_factory):
    _prepare_week(session_factory)
    monday_next = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    # Оценка за день запроса: без неё /plan уходит в подсказку и ничего не
    # пишет — тест проверял бы пустоту.
    with session_factory() as session:
        repository.save_wellness(session, monday_next.date(), 4, None, monday_next)
    wellness = WellnessService(session_factory, ZONE, clock=lambda: monday_next)

    before = _counts(session_factory)
    wellness.weekly_review()
    before_plan = _counts(session_factory)
    wellness.show_plan(monday_next)
    after_plan = _counts(session_factory)

    assert before_plan == before
    # Снимок — единственная запись показа плана (R-36); расписание и
    # напоминания те же (R-24).
    with session_factory() as session:
        snapshots = session.scalar(select(func.count()).select_from(PlanSnapshot))
    assert snapshots == 2  # снимок прошлой недели + новый
    assert {k: v for k, v in after_plan.items()} == before_plan


def test_week_command_does_not_call_model(session_factory):
    _prepare_week(session_factory)
    monday_next = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    wellness = WellnessService(session_factory, ZONE, clock=lambda: monday_next)
    bot = FakeBot()

    update = make_update("/week")
    context = make_context(
        {"wellness_service": wellness, OWNER_KEY: 42}, bot=bot
    )
    asyncio.run(cmd_week(update, context))

    # R-37: разбор — арифметика, а не запрос к модели.
    assert bot.actions == []
    assert update.message.sent
