"""Тесты клавиатур: подписи и callback_data, без похода в Telegram.

`InlineKeyboardMarkup` из python-telegram-bot — обычный объект с полями,
поэтому проверять его можно как структуру данных.
"""

from datetime import date

from src.bot.keyboards import reminder_keyboard, wellness_keyboard
from src.core.reminders import CheckinAction, parse_checkin_callback
from src.core.wellness import parse_wellness_callback


def test_reminder_keyboard_is_one_row_with_two_buttons():
    rows = reminder_keyboard(7).inline_keyboard

    assert len(rows) == 1
    assert len(rows[0]) == 2


def test_reminder_keyboard_labels_are_facts_not_intentions():
    done, skip = reminder_keyboard(7).inline_keyboard[0]

    # «сделал / пропустил» — про уже случившееся. «Готово / пропустить»
    # читается как намерение и путает: непонятно, отмечают факт или план.
    assert done.text == "сделал"
    assert skip.text == "пропустил"


def test_reminder_keyboard_callbacks_carry_the_reminder_id():
    done, skip = reminder_keyboard(13).inline_keyboard[0]

    assert done.callback_data == "ci:13:done"
    assert skip.callback_data == "ci:13:skip"


def test_every_button_of_the_keyboard_is_parsable():
    # Связка «клавиатура → разбор» проверяется целиком: клавиатура и разбор
    # живут в разных слоях и легко разъезжаются молча.
    for button in reminder_keyboard(21).inline_keyboard[0]:
        reminder_id, action = parse_checkin_callback(button.callback_data)

        assert reminder_id == 21
        assert action in CheckinAction


def test_wellness_keyboard_has_five_scores_in_one_row():
    rows = wellness_keyboard(date(2026, 10, 27)).inline_keyboard

    assert len(rows) == 1
    assert [button.text for button in rows[0]] == ["1", "2", "3", "4", "5"]


def test_wellness_keyboard_callbacks_carry_day_and_score():
    for button in wellness_keyboard(date(2026, 10, 27)).inline_keyboard[0]:
        day, score = parse_wellness_callback(button.callback_data)

        assert day == date(2026, 10, 27)
        assert score in (1, 2, 3, 4, 5)
