"""Сборка приложения: база, сервис, хендлеры и планировщик.

Здесь видно, из чего состоит бот, но нет ни одной бизнес-операции: каждая
часть проверяется отдельно, а этот модуль только соединяет их проводами.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler

from src.bot.access import OWNER_KEY
from src.bot.callbacks import on_checkin
from src.bot.handlers import cmd_history, cmd_list, cmd_new, cmd_off, on_free_text
from src.bot.service import WorkoutService
from src.bot.telegram_sender import TelegramSender
from src.config import Settings
from src.core.reminders import ReminderSender
from src.db.session import create_db_engine, init_db, make_session_factory
from src.scheduler.runner import build_scheduler
from src.scheduler.tick import SchedulerTick


@dataclass
class Runtime:
    """Долгоживущие части: база и сервис, независимые от Telegram."""

    engine: Engine
    session_factory: sessionmaker[Session]
    service: WorkoutService


def build_runtime(settings: Settings) -> Runtime:
    """Открыть базу и создать таблицы. Повторный запуск безопасен."""
    engine = create_db_engine(settings.db_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    return Runtime(
        engine=engine,
        session_factory=session_factory,
        service=WorkoutService(session_factory, settings.zone),
    )


def build_tick(runtime: Runtime, sender: ReminderSender, settings: Settings) -> SchedulerTick:
    """Тик с настройками пользователя: таймзона и окно ожидания ответа."""
    miss_window = (
        timedelta(hours=settings.miss_window_hours)
        if settings.miss_window_hours is not None
        else None
    )
    return SchedulerTick(
        runtime.session_factory,
        sender,
        settings.zone,
        miss_window=miss_window,
    )


def register_handlers(application: Application) -> None:
    """Команды бота и кнопки под напоминаниями."""
    application.add_handler(CommandHandler("new", cmd_new))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("off", cmd_off))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CallbackQueryHandler(on_checkin, pattern=r"^ci:"))
    # Свободный текст принимаем только от владельца (проверка внутри on_free_text)
    application.add_handler(MessageHandler(lambda update, context: True, on_free_text))



def build_application(settings: Settings, runtime: Runtime | None = None) -> Application:
    """Собрать приложение. Планировщик стартует в `post_init`, не здесь.

    Так тесты и `--dry-run` могут собрать приложение, ничего не запуская.
    """
    runtime = runtime or build_runtime(settings)
    application = (
        Application.builder().token(settings.bot_token.get_secret_value()).build()
    )
    application.bot_data["workout_service"] = runtime.service
    # Один пользователь — один id. Все входы сверяются с ним (R-05).
    application.bot_data[OWNER_KEY] = settings.owner_id

    async def start_scheduler(app: Application) -> None:
        # Отправитель создаётся здесь: `Bot` существует только после сборки
        # приложения, а тику нужен уже готовый транспорт.
        sender = TelegramSender(app.bot, settings.owner_id, settings.zone)
        scheduler = build_scheduler(build_tick(runtime, sender, settings))
        scheduler.start()
        app.bot_data["scheduler"] = scheduler

    async def stop_scheduler(app: Application) -> None:
        scheduler = app.bot_data.pop("scheduler", None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    application.post_init = start_scheduler
    application.post_shutdown = stop_scheduler
    register_handlers(application)
    return application


def main() -> None:
    """Точка входа процесса: конфиг, приложение, long polling."""
    build_application(Settings()).run_polling()
