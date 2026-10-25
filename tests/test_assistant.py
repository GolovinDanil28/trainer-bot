"""Тесты parse_intent из src.core.assistant.

Каждый тест проверяет одно действие (create/list/off/none) и возврат
AssistantIntent с правильными полями.
"""

import pytest

from src.core.assistant import (
    AssistantAction,
    IntentError,
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