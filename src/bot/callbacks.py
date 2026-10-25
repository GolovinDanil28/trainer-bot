"""Кнопки под напоминанием: «сделал» и «пропустил» (R-11, R-12).

Слой тонкий: разбор callback_data — в домене, запись — в сервисе. Здесь только
проверка доступа и два ответа Telegram (всплывающий текст и снятие клавиатуры).
"""

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.access import is_owner
from src.bot.service import WorkoutService
from src.core.reminders import CallbackError, parse_checkin_callback


async def on_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    if not is_owner(update, context):
        # Чужому не объясняем, почему нельзя: сам факт живого бота — лишняя
        # информация. Ответ пустой, как и у невалидной кнопки.
        await query.answer()
        return

    try:
        reminder_id, action = parse_checkin_callback(query.data or "")
    except CallbackError:
        # Старый формат или подделка: отвечать нечего, журнал не трогаем.
        await query.answer()
        return

    service: WorkoutService = context.bot_data["workout_service"]
    text = service.record_checkin(reminder_id, action)

    # Текст — всплывающим ответом, а клавиатуру снимаем: отметка уже сделана,
    # и повторное нажатие всё равно вернёт «уже отмечено».
    await query.answer(text)
    await query.edit_message_reply_markup(reply_markup=None)
