"""Тесты parse_intent из src.core.assistant.

Каждый тест проверяет одно действие (create/list/off/none) и возврат
AssistantIntent с правильными полями.
"""

import pytest

from src.core.assistant import (
    AssistantAction,
    IntentError,
    build_system_prompt,
    build_user_prompt,
    parse_intent,
)
from src.core.schedule import Weekday, WorkoutType


@pytest.mark.parametrize(
    "json_str, expected_action",
    [
        ('{"action":"create","type":"strength","days":["Пн"],"time":"08:00"}',
         AssistantAction.CREATE),
        ('{"action":"list"}', AssistantAction.LIST),
        ('{"action":"off","workout_id":2}', AssistantAction.OFF),
        ('{"action":"none"}', AssistantAction.NONE),
    ],
)
def test_parse_intent_returns_correct_action(json_str, expected_action):
    result = parse_intent(json_str)
    assert result.action == expected_action


def test_parse_intent_create_has_type_and_days_and_time():
    result = parse_intent(
        '{"action":"create","type":"cardio","days":["Вт","Чт"],"time":"19:30"}'
    )
    assert result.action == AssistantAction.CREATE
    assert result.type == WorkoutType.CARDIO  # type: ignore
    assert result.days == [Weekday.TUESDAY, Weekday.THURSDAY]  # type: ignore
    assert result.time == "19:30"
    assert result.workout_id is None


def test_parse_intent_off_has_workout_id():
    result = parse_intent('{"action":"off","workout_id":5}')
    assert result.action == AssistantAction.OFF
    assert result.workout_id == 5


def test_parse_intent_none_has_no_extra_fields():
    result = parse_intent('{"action":"none"}')
    assert result.action == AssistantAction.NONE
    assert result.type is None
    assert result.days is None
    assert result.time is None
    assert result.workout_id is None


def test_parse_intent_raises_on_bad_json():
    with pytest.raises(IntentError):
        parse_intent("not json")


def test_parse_intent_raises_on_create_without_type():
    with pytest.raises(IntentError):
        parse_intent('{"action":"create","days":["Пн"],"time":"10:00"}')


def test_parse_intent_raises_on_create_without_days():
    with pytest.raises(IntentError):
        parse_intent('{"action":"create","type":"cardio","time":"10:00"}')


def test_parse_intent_raises_on_create_without_time():
    with pytest.raises(IntentError):
        parse_intent('{"action":"create","type":"mobility","days":["Пн"]}')


def test_parse_intent_raises_on_off_with_bad_id():
    with pytest.raises(IntentError):
        parse_intent('{"action":"off","workout_id":0}')


def test_parse_intent_normalizes_full_day_names():
    result = parse_intent(
        '{"action":"create","type":"mobility","days":["понедельник","вторник"],"time":"09:00"}'
    )
    from src.core.schedule import Weekday
    assert result.days == [Weekday.MONDAY, Weekday.TUESDAY]


def test_parse_intent_validates_time_range():
    with pytest.raises(IntentError):
        parse_intent('{"action":"create","type":"strength","days":["Пн"],"time":"25:00"}')


def test_parse_intent_rejects_invalid_day_saturday():
    with pytest.raises(IntentError):
        parse_intent('{"action":"create","type":"strength","days":["Сббота"],"time":"10:00"}')


def test_parse_intent_rejects_unknown_action():
    with pytest.raises(IntentError):
        parse_intent('{"action":"random","type":"strength","days":["Пн"],"time":"10:00"}')


def test_parse_intent_sorts_and_deduplicates_days():
    # 'Пт,пн,Пятница' → сортировка и дедупликация → [Пн, Пт]
    result = parse_intent(
        '{"action":"create","type":"mobility","days":["Пт","пн","Пятница"],"time":"07:00"}'
    )
    from src.core.schedule import Weekday
    assert result.days == [Weekday.MONDAY, Weekday.FRIDAY]


def test_system_prompt_has_contract_and_time_anchor():
    prompt = build_system_prompt()

    assert "action" in prompt
    assert "workout_id" in prompt
    # Пример-якорь из D4: без него «половина седьмого вечера» уходит в 19:30.
    assert "половина седьмого вечера" in prompt.lower()
    assert "18:30" in prompt
    assert "none" in prompt


def test_user_prompt_contains_text_and_snapshot():
    prompt = build_user_prompt("убери вторую тренировку", "#1 — силовая, Пн 19:30")

    assert "убери вторую тренировку" in prompt
    assert "#1 — силовая, Пн 19:30" in prompt


def test_user_prompt_marks_empty_schedule():
    prompt = build_user_prompt("покажи расписание", "   ")

    assert "расписание пусто" in prompt.lower()


@pytest.mark.parametrize(
    ("raw_json", "text"),
    [
        pytest.param(
            '{"action":"create","type":"mobility","days":["Ср"],"time":"08:00"}',
            "мобильность в среду утром",
            id="vague-time",
        ),
        pytest.param(
            '{"action":"create","type":"cardio","days":["Вт"],"time":"12:00"}',
            "хочу тренироваться по вторникам в 12:00",
            id="invented-type",
        ),
        pytest.param(
            '{"action":"create","type":"strength","days":["Пн"],"time":"19:30"}',
            "силовая завтра",
            id="invented-time-and-days",
        ),
        pytest.param(
            '{"action":"off","workout_id":2}', "завтра не смогу", id="off-without-verb"
        ),
        pytest.param(
            '{"action":"off","workout_id":1}',
            "в пятницу вместо силовой будет кардио",
            id="replacement-as-off",
        ),
    ],
)
def test_grounding_rejects_fields_not_in_text(raw_json, text):
    # Модель не имеет права добавлять тип, дни или время, которых нет в фразе.
    with pytest.raises(IntentError):
        parse_intent(raw_json, text)


@pytest.mark.parametrize(
    ("raw_json", "text"),
    [
        pytest.param(
            '{"action":"create","type":"cardio","days":["Вт"],"time":"08:00"}',
            "кардио по вторникам в восемь утра",
            id="spoken-time",
        ),
        pytest.param(
            '{"action":"create","type":"mobility","days":["Вс"],"time":"21:00"}',
            "напоминай про растяжку каждый вечер в 21:00",
            id="stretch-synonym",
        ),
        pytest.param(
            '{"action":"off","workout_id":2}', "убери вторую тренировку", id="off-verb"
        ),
    ],
)
def test_grounding_accepts_real_phrases(raw_json, text):
    intent = parse_intent(raw_json, text)

    assert intent.action is not None


def test_every_day_fills_all_weekdays():
    intent = parse_intent(
        '{"action":"create","type":"cardio","time":"07:00"}',
        "кардио каждый день в 7:00",
    )

    assert intent.days == list(Weekday)