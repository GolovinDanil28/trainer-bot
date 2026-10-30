"""Кнопки под напоминанием: «сделал» и «пропустил» (R-11, R-12).

Слой тонкий: разбор callback_data — в домене, запись — в сервисе. Здесь только
проверка доступа и два ответа Telegram (всплывающий текст и снятие клавиатуры).
"""

from datetime import time

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.access import REFUSAL_TEXT, is_owner
from src.bot.pending import ChoiceStatus, PendingChoices
from src.bot.service import WorkoutService
from src.bot.wellness_service import WellnessService
from src.core.choice import parse_type_choice_callback
from src.core.messages import CHOICE_DONE_TEXT, CHOICE_EXPIRED_TEXT
from src.core.reminders import CallbackError, parse_checkin_callback
from src.core.wellness import parse_wellness_callback

# Вариантов «или» максимум 4 (R-34); индекс 0..3 — всё остальное подделка.
_MAX_OPTIONS_INDEX = 3


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


async def on_type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки уточнения типа: создаёт тренировку только выбор владельца (R-34)."""
    query = update.callback_query
    if query is None:
        return

    if not is_owner(update, context):
        # Отказ тот же, что у свободного текста (R-18): один класс правил доступа.
        await query.answer(REFUSAL_TEXT)
        return

    try:
        pending_id, index = parse_type_choice_callback(query.data or "")
    except CallbackError:
        await query.answer()
        return

    if index > _MAX_OPTIONS_INDEX:
        # Индекс за пределами массива вариантов: отвечаем, но уточнение не
        # съедаем — иначе подделанная кнопка отменит настоящее ожидание.
        await query.answer(CHOICE_EXPIRED_TEXT)
        return

    pending: PendingChoices = context.bot_data["pending_choices"]
    choice, status = pending.take(pending_id)

    if status is ChoiceStatus.DONE:
        await query.answer(CHOICE_DONE_TEXT)
        return
    if choice is None:
        # Истекло или процесс перезапускался: молча ничего не создаём.
        await query.answer(CHOICE_EXPIRED_TEXT)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    options = choice.intent.type_options
    if not 0 <= index < len(options):
        await query.answer(CHOICE_EXPIRED_TEXT)
        return

    moment = time.fromisoformat(choice.intent.time or "00:00")
    service: WorkoutService = context.bot_data["workout_service"]
    if choice.intent.date_raw is not None:
        # «или» + дата: тип выбирается кнопкой, дата остаётся из фразы (R-34).
        text = service.create_once(
            choice.intent.date_raw,
            options[index],
            moment,
            choice.intent.distance_km,
            choice.intent.duration_min,
        )
    else:
        text = service.create_weekly(
            options[index],
            tuple(choice.intent.days or ()),
            moment,
            choice.intent.distance_km,
            choice.intent.duration_min,
        )
    pending.mark_done(pending_id)

    await query.answer()
    await query.edit_message_text(text, reply_markup=None)
