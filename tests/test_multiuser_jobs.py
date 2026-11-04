"""Тики по нескольким пользователям: доставка, время, изоляция (R-06, R-19, R-40)."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.core.schedule import Weekday, make_weekly_draft
from src.db import repository
from src.db.models import User
from src.scheduler.multi import JobFactories, MultiUserJobs
from tests.fakes import FakePromptSender, FakeSender

ZONE = ZoneInfo("UTC")
MONDAY = datetime(2026, 11, 2, 19, 30, tzinfo=UTC)
SUNDAY = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)


def _register(session_factory, telegram_id: int, **overrides):
    values = {
        "tz": "UTC",
        "wellness_time": "20:00",
        "week_review_time": "20:00",
    }
    values.update(overrides)
    with session_factory() as session:
        return repository.create_user(session, telegram_id, **values)


def _workout(session_factory, user_id: int, at=time(19, 30)) -> int:
    from src.db.context import acting_as

    with session_factory() as session, acting_as(user_id):
        return repository.create_workout(
            session, make_weekly_draft("бег", (Weekday.MONDAY,), at)
        ).id


class ReportSender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


def _factories(reminders, prompts, reports, render=lambda user, now: f"review {user.id}"):
    return JobFactories(
        reminder_sender=lambda user: reminders.setdefault(user.id, FakeSender()),
        wellness_sender=lambda user: prompts.setdefault(user.id, FakePromptSender()),
        report_sender=lambda user: reports.setdefault(user.id, ReportSender()).send_text,
        render_weekly=render,
    )


async def test_reminders_go_to_each_owner(session_factory):
    first = _register(session_factory, 42)
    second = _register(session_factory, 555)
    _workout(session_factory, first.id)
    _workout(session_factory, second.id)
    reminders: dict[int, FakeSender] = {}
    jobs = MultiUserJobs(session_factory, _factories(reminders, {}, {}))

    await jobs.run_reminders(MONDAY)

    assert len(reminders[first.id].sent) == 1
    assert len(reminders[second.id].sent) == 1
    assert reminders[first.id].sent[0].workout_id != reminders[second.id].sent[0].workout_id


async def test_failure_of_one_user_does_not_stop_others(session_factory):
    first = _register(session_factory, 42)
    second = _register(session_factory, 555)
    _workout(session_factory, first.id)
    _workout(session_factory, second.id)
    reminders = {first.id: FakeSender(failures=5)}

    def sender_for(user):
        return reminders.setdefault(user.id, FakeSender())

    factories = JobFactories(
        reminder_sender=sender_for,
        wellness_sender=lambda user: FakePromptSender(),
        report_sender=lambda user: ReportSender().send_text,
        render_weekly=lambda user, now: "review",
    )
    jobs = MultiUserJobs(session_factory, factories)

    await jobs.run_reminders(MONDAY)

    assert reminders[second.id].sent, "второй пользователь должен получить своё"


async def test_wellness_uses_each_users_time(session_factory):
    early = _register(session_factory, 42, wellness_time="20:00")
    late = _register(session_factory, 555, wellness_time="22:00")
    prompts: dict[int, FakePromptSender] = {}
    jobs = MultiUserJobs(session_factory, _factories({}, prompts, {}))

    await jobs.run_wellness(datetime(2026, 11, 2, 20, 0, tzinfo=UTC))

    assert prompts[early.id].sent and not prompts[late.id].sent

    await jobs.run_wellness(datetime(2026, 11, 2, 22, 0, tzinfo=UTC))

    assert prompts[late.id].sent


async def test_weekly_review_for_each_user_and_skips_disabled(session_factory):
    first = _register(session_factory, 42)
    second = _register(session_factory, 555)
    disabled = _register(session_factory, 777)
    with session_factory() as session:
        user = session.scalar(select(User).where(User.id == disabled.id))
        assert user is not None
        user.is_active = False
        session.commit()

    reports: dict[int, ReportSender] = {}
    jobs = MultiUserJobs(session_factory, _factories({}, {}, reports))

    await jobs.run_weekly(SUNDAY)

    assert reports[first.id].sent == [f"review {first.id}"]
    assert reports[second.id].sent == [f"review {second.id}"]
    assert disabled.id not in reports


async def test_user_timezone_is_used(session_factory):
    # 20:00 по Екатеринбургу = 15:00 UTC.
    user = _register(session_factory, 42, tz="Asia/Yekaterinburg", wellness_time="20:00")
    prompts: dict[int, FakePromptSender] = {}
    jobs = MultiUserJobs(session_factory, _factories({}, prompts, {}))

    await jobs.run_wellness(datetime(2026, 11, 2, 15, 0, tzinfo=UTC))

    assert prompts[user.id].sent
