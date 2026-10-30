"""Уточнение типа кнопками (R-34): формат callback, память, конец-в-конец."""

import pytest
from sqlalchemy import func, select

from src.bot.access import OWNER_KEY, REFUSAL_TEXT
from src.bot.assistant_service import AssistantService
from src.bot.callbacks import on_type_choice
from src.bot.handlers import on_free_text
from src.bot.pending import ChoiceStatus, PendingChoices
from src.core.assistant import IntentError, parse_intent
from src.core.choice import build_type_choice_callback, parse_type_choice_callback
from src.core.messages import CHOICE_DONE_TEXT, CHOICE_EXPIRED_TEXT
from src.core.reminders import CallbackError
from src.core.schedule import Weekday
from src.db.models import Workout
from tests.fakes import FakeBot, FakeLLM, make_callback_update, make_context, make_update

OWNER_ID = 42

CHOICE_JSON = (
    '{"action":"create","type":null,'
    '"type_options":["бег","интервальный бег"],'
    '"days":["Ср"],"time":"19:00","distance_km":7}'
)
PHRASE = "бег или интервальный бег по средам в 19:00 на 7 км"


@pytest.fixture
def pending():
    return PendingChoices()


def assistant_context(service, llm, pending: PendingChoices, bot: FakeBot | None = None):
    return make_context(
        {
            "workout_service": service,
            "assistant_service": AssistantService(llm, service, "test", 1.0),
            "pending_choices": pending,
            OWNER_KEY: OWNER_ID,
        },
        bot=bot if bot is not None else FakeBot(),
    )


# --- Формат кнопки --------------------------------------------------------


def test_callback_roundtrip():
    data = build_type_choice_callback("abc12345", 1)

    assert parse_type_choice_callback(data) == ("abc12345", 1)


@pytest.mark.parametrize(
    "data", ["", "tc:", "tc:id", "tc:id:x", "ci:id:1", "tc::1", "tc:id:-1"]
)
def test_bad_callback_is_rejected(data):
    with pytest.raises(CallbackError):
        parse_type_choice_callback(data)


# --- Память уточнений -----------------------------------------------------


def _intent():
    return parse_intent(CHOICE_JSON, PHRASE)


def test_pending_take_once():
    pending = PendingChoices()
    choice = pending.put(42, _intent())

    taken, status = pending.take(choice.id)

    assert status is ChoiceStatus.PENDING
    assert taken is choice
    assert pending.take(choice.id) == (None, ChoiceStatus.MISSING)


def test_pending_done_marker():
    pending = PendingChoices()
    choice = pending.put(42, _intent())
    pending.take(choice.id)
    pending.mark_done(choice.id)

    assert pending.take(choice.id) == (None, ChoiceStatus.DONE)


def test_pending_expires():
    now = [0.0]
    pending = PendingChoices(ttl_seconds=600, clock=lambda: now[0])
    choice = pending.put(42, _intent())

    now[0] = 601.0

    assert pending.take(choice.id) == (None, ChoiceStatus.MISSING)


# --- Конец-в-конец через поддельный Telegram ------------------------------


async def test_phrase_with_or_asks_and_does_not_write(
    service, session_factory, pending
):
    llm = FakeLLM(answers={PHRASE: CHOICE_JSON})
    update = make_update(PHRASE)

    await on_free_text(update, assistant_context(service, llm, pending))

    assert len(update.message.sent) == 1
    assert "Уточни" in update.message.sent[0]
    assert update.message.markups[0] is not None
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0


async def test_choice_creates_one_workout_and_second_click_is_safe(
    service, session_factory, pending
):
    llm = FakeLLM(answers={PHRASE: CHOICE_JSON})
    update = make_update(PHRASE)
    context = assistant_context(service, llm, pending)
    await on_free_text(update, context)

    pending_id = next(iter(pending._items))
    click = make_callback_update(build_type_choice_callback(pending_id, 1))

    await on_type_choice(click, context)

    with session_factory() as session:
        workouts = session.scalars(select(Workout)).all()
    assert len(workouts) == 1
    assert workouts[0].workout_type == "интервальный бег"
    assert workouts[0].duration_min == 70
    assert click.callback_query.text_edits  # сообщение заменено подтверждением

    again = make_callback_update(build_type_choice_callback(pending_id, 1))
    await on_type_choice(again, context)

    assert again.callback_query.answers == [CHOICE_DONE_TEXT]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 1


async def test_expired_choice_creates_nothing(service, session_factory, pending):
    now = [0.0]
    pending._clock = lambda: now[0]
    llm = FakeLLM(answers={PHRASE: CHOICE_JSON})
    context = assistant_context(service, llm, pending)
    await on_free_text(make_update(PHRASE), context)
    pending_id = next(iter(pending._items))

    now[0] = 601.0
    click = make_callback_update(build_type_choice_callback(pending_id, 0))
    await on_type_choice(click, context)

    assert click.callback_query.answers == [CHOICE_EXPIRED_TEXT]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0


async def test_stranger_click_is_refused(service, session_factory, pending):
    llm = FakeLLM(answers={PHRASE: CHOICE_JSON})
    context = assistant_context(service, llm, pending)
    await on_free_text(make_update(PHRASE), context)
    pending_id = next(iter(pending._items))

    click = make_callback_update(
        build_type_choice_callback(pending_id, 0), user_id=999
    )
    await on_type_choice(click, context)

    assert click.callback_query.answers == [REFUSAL_TEXT]
    assert click.callback_query.text_edits == []
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Workout)) == 0


# --- Разбор вариантов модели ---------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        ["бег"],
        ["бег", "кардио", "бег", "бег", "бег"],
        ["бег", "бег"],
        ["бег", ""],
        ["бег", "б"],
        ["бег", 7],
        "бег",
    ],
)
def test_bad_type_options_are_rejected(options):
    raw = (
        '{"action":"create","type":null,'
        f'"type_options":{options!r},'
        '"days":["Ср"],"time":"19:00"}'
    ).replace("'", '"')

    with pytest.raises(IntentError):
        parse_intent(raw, PHRASE)


def test_option_must_be_mentioned_in_text():
    with pytest.raises(IntentError):
        parse_intent(
            '{"action":"create","type":null,'
            '"type_options":["плавание","йога"],'
            '"days":["Ср"],"time":"19:00"}',
            "бег по средам в 19:00",
        )


def test_choice_time_is_used_for_creation():
    # Кнопка несёт только тип; остальное берётся из запомненного намерения.
    intent = _intent()

    assert intent.time == "19:00"
    assert intent.distance_km == 7
    assert intent.days == [Weekday.WEDNESDAY]
