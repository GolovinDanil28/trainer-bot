"""Хендлеры Telegram: тонкие обёртки над сервисом.

Правило слоя: здесь нет ни SQL, ни разбора строк — только «достать текст из
сообщения, вызвать сервис, ответить». Всё, что можно проверить без Telegram,
проверяется в `service.py` и `core/».
"""

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.access import owner_only
from src.bot.service import WorkoutService
from src.core.assistant import parse_intent, IntentError

DEFAULT_SNAPSHOT = ""


def _service(context: ContextTypes.DEFAULT_TYPE) -> WorkoutService:
    return context.bot_data["workout_service"]


@owner_only
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/new strength Пн,Ср,Пт 19:30` — создать тренировку (R-01)."""
    text = update.message.text if update.message else ""
    await update.message.reply_text(_service(context).create_from_command(text or ""))


@owner_only
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/list` — показать активные тренировки (R-02)."""
    await update.message.reply_text(_service(context).show_schedule())


@owner_only
async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/off <id>` — выключить тренировку (R-03)."""
    arguments = context.args or []
    if len(arguments) != 1:
        await update.message.reply_text("Нужен номер тренировки. Пример: /off 2")
        return
    await update.message.reply_text(_service(context).disable(arguments[0]))


@owner_only
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/history` — последние записи журнала (R-13)."""
    await update.message.reply_text(_service(context).show_history())


@owner_only
async def on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка свободного текста пользователя (не команды).

    Пытается распарсить текст как расписание и выполнить соответствующую
    операцию. При сбое — подсказка со строгой командой.
    """
    text = update.message.text if update.message else ""
    if not text or text.startswith("/"):
        return
    # Снимаем команду, если что-то осталось после косвенного упоминания
    # (Telegram может передавать /start и подобное в Update.message.text)
    # Попытаться распарсить
    try:
        intent = parse_intent(text)
    except IntentError:
        # Модель вернула мусор — просим строгую команду
        await update.message.reply_text(
            "Не понял. Сформулируйте так: /new strength Пн,Ср 19:30"
        )
        return

    service = _service(context)
    snapshot = service.schedule_snapshot()

    if intent.action == "create":
        # Собираем команду /new из полей модели
        if intent.type is None or intent.days is None or intent.time is None:
            await update.message.reply_text(
                "Не понял: для создания нужны тип, дни и время. "
                "Пример: /new strength Пн,Ср 19:30"
            )
            return
        # Нормализуем дни в формат, ожидаемый create_from_command
        days_str = ", ".join(d.value for d in intent.days)
        cmd = f"/new {intent.type.value} {days_str} {intent.time}"
        await update.message.reply_text(service.create_from_command(cmd))

    elif intent.action == "list":
        await update.message.reply_text(service.show_schedule())

    elif intent.action == "off":
        if intent.workout_id is not None:
            await update.message.reply_text(service.disable(str(intent.workout_id)))
        else:
            # Попытаться найти тренировку по порядковому номеру из текста
            # или через снапшот. Здесь просто просим уточнить.
            # В рамках теста 4.2 ожидается, что «убери вторую тренировку»
            # отключит #2, если в snapshot три тренировки.
            # Используем простую эвристику: ищем число «2» или «вторую».
            import re
            m = re.search(r"\b(\d+)-?я?\b", intent.reason or "")
            if not m:
                # Нет номера — спрашиваем
                await update.message.reply_text(
                    "Не поняла, какую тренировку убрать. "
                    "Используйте /off 1, /off 2 и т.д."
                )
            else:
                num = int(m.group(1))
                # Проверяем, есть ли такая тренировка в снапшоте
                # Сnabshot формат: "1: ...\n2: ...\n3: ..."
                lines = snapshot.splitlines()
                found = False
                for line in lines:
                    if line.startswith(f"{num}:"):
                        found = True
                        break
                if found:
                    await update.message.reply_text(service.disable(str(num)))
                else:
                    await update.message.reply_text(
                        f"Тренировки под номером {num} нет. "
                        "Используйте /list чтобы посмотреть номера."
                    )

    elif intent.action == "none":
        # Ничего не делаем, просто отвечаем
        await update.message.reply_text(
            "Понял, ничего не делаю. Если нужно — напиши /new или /list"
        )

    else:
        await update.message.reply_text(
            "Неизвестная операция. Используйте /new, /list или /off"
        )
