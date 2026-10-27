"""Кнопки под напоминанием: «сделал» и «пропустил» (R-11, R-12).

Слой тонкий: разбор callback_data — в домене, запись — в сервисе. Здесь только
проверка доступа и два ответа Telegram (всплывающий текст и снятие клавиатуры).
"""

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.access import is_owner
from src.bot.service import WorkoutService
from src.bot.wellness_service import WellnessService
from src.core.reminders import CallbackError, parse_checkin_callback
from src.core.wellness import parse_wellness_callback


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


async def on_wellness(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки 1–5 под вечерним вопросом (R-20, R-22)."""
    query = update.callback_query
    if query is None:
        return

    if not is_owner(update, context):
        # Как и у отметок: чужому не объясняем, почему нельзя.
        await query.answer()
        return

    try:
        day, score = parse_wellness_callback(query.data or "")
    except CallbackError:
        await query.answer()
        return

    service: WellnessService = context.bot_data["wellness_service"]
    result = service.record_from_button(day, score)

    if not result.accepted:
        # Вопрос другого дня: отвечаем всплывающим текстом и снимаем клавиатуру,
        # но не переписываем старую карточку — она часть истории чата.
        await query.answer(result.text)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # Свежая оценка: карточка вопроса превращается в подтверждение с планом.
    # `reply_markup=None` снимает клавиатуру явно: иначе кнопки остаются живыми
    # под «Записал 3/5», и оценку можно переписать, не видя, что это уже ответ.
    await query.answer()
    await query.edit_message_text(result.text, reply_markup=None)
