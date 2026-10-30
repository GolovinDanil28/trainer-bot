"""Сквозной сценарий MVP: `/new` → тик → отправка → кнопка → `/history` (6.2).

Telegram не поднимается: клавиатура собирается настоящая, транспорт —
`FakeSender`. Время фиксировано, иначе тест зависел бы от дня недели и падал
в новогоднюю ночь.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from src.bot.access import OWNER_KEY
from src.bot.callbacks import on_checkin
from src.bot.handlers import cmd_history, cmd_new
from src.bot.keyboards import reminder_keyboard
from src.bot.service import WorkoutService
from src.core.reminders import ReminderStatus
from src.core.schedule import WorkoutType
from src.db.models import Reminder
from src.scheduler.tick import SchedulerTick
from tests.fakes import FakeSender, make_callback_update, make_context, make_update

ZONE = ZoneInfo("Asia/Yekaterinburg")
# Понедельник 26.10.2026, 19:30 по Екатеринбургу — ровно момент тренировки.
MOMENT = datetime(2026, 10, 26, 14, 30, tzinfo=UTC)


async def test_new_tick_tap_history(session_factory):
    service = WorkoutService(session_factory, ZONE)
    context = make_context({"workout_service": service, OWNER_KEY: 42})

    created = make_update("/new strength Пн 19:30")
    await cmd_new(created, context)
    assert created.message.sent == ["Готово: #1 — силовая, Пн, 19:30 (45 мин)."]

    sender = FakeSender()
    tick = SchedulerTick(session_factory, sender, ZONE)
    first = await tick.run(MOMENT)

    assert first.claimed == [1]
    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert message.workout_type == WorkoutType.STRENGTH
    assert message.scheduled_at_utc == MOMENT

    # Повторный тик в том же окне не отправляет второй раз (R-06).
    second = await tick.run(MOMENT)
    assert second.claimed == []
    assert len(sender.sent) == 1

    # Нажимаем «сделал» — данные берём из настоящей клавиатуры, а не из строки.
    done_button = reminder_keyboard(message.reminder_id).inline_keyboard[0][0]
    tap = make_callback_update(done_button.callback_data)
    await on_checkin(tap, context)
    assert tap.callback_query.answers == ["Записал: сделал."]

    history = make_update("/history")
    await cmd_history(history, context)
    assert history.message.sent == [
        "Последние записи:\n26.10.2026 19:30 — силовая, сделал"
    ]

    with session_factory() as session:
        reminder = session.get(Reminder, message.reminder_id)
        assert reminder.status == ReminderStatus.ANSWERED.value
