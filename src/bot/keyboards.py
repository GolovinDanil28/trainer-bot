"""Клавиатуры: единственное место, где из данных рождается разметка Telegram."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.core.reminders import CHECKIN_LABELS, CheckinAction, checkin_callback


def reminder_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    """Две кнопки под напоминанием: «сделал» и «пропустил» (R-07, R-11)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    CHECKIN_LABELS[CheckinAction.DONE],
                    callback_data=checkin_callback(reminder_id, CheckinAction.DONE),
                ),
                InlineKeyboardButton(
                    CHECKIN_LABELS[CheckinAction.SKIP],
                    callback_data=checkin_callback(reminder_id, CheckinAction.SKIP),
                ),
            ]
        ]
    )
