"""Сборка приложения: база, сервис, хендлеры и планировщик.

Здесь видно, из чего состоит бот, но нет ни одной бизнес-операции: каждая
часть проверяется отдельно, а этот модуль только соединяет их проводами.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.bot.access import OWNER_KEY
from src.bot.assistant_service import AssistantService
from src.bot.callbacks import on_checkin, on_wellness
from src.bot.handlers import (
    cmd_help,
    cmd_history,
    cmd_list,
    cmd_new,
    cmd_off,
    cmd_plan,
    cmd_start,
    cmd_wellness,
    on_free_text,
)
from src.bot.service import WorkoutService
from src.bot.telegram_sender import TelegramSender, TelegramWellnessSender
from src.bot.wellness_service import WellnessService
from src.config import Settings
from src.core.reminders import ReminderSender
from src.db.session import create_db_engine, init_db, make_session_factory
from src.llm.ollama import OllamaClient
from src.scheduler.runner import build_scheduler
from src.scheduler.tick import SchedulerTick
from src.scheduler.wellness_tick import WellnessTick


@dataclass
class Runtime:
    """Долгоживущие части: база и сервисы, независимые от Telegram."""

    engine: Engine
    session_factory: sessionmaker[Session]
    service: WorkoutService
    wellness: WellnessService


def build_runtime(settings: Settings) -> Runtime:
    """Открыть базу и создать таблицы. Повторный запуск безопасен."""
    engine = create_db_engine(settings.db_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    return Runtime(
        engine=engine,
        session_factory=session_factory,
        service=WorkoutService(session_factory, settings.zone),
        wellness=WellnessService(session_factory, settings.zone),
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


def build_wellness_tick(
    runtime: Runtime, sender: TelegramWellnessSender, settings: Settings
) -> WellnessTick:
    """Тик вечернего вопроса: своё время из конфига, та же таймзона."""
    return WellnessTick(
        runtime.session_factory,
        sender,
        settings.zone,
        settings.wellness_at,
    )


def register_handlers(application: Application) -> None:
    """Команды бота и кнопки под сообщениями."""
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("new", cmd_new))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("off", cmd_off))
    application.add_handler(CommandHandler("history", cmd_history))
    application.add_handler(CommandHandler("wellness", cmd_wellness))
    application.add_handler(CommandHandler("plan", cmd_plan))
    application.add_handler(CallbackQueryHandler(on_checkin, pattern=r"^ci:"))
    application.add_handler(CallbackQueryHandler(on_wellness, pattern=r"^wl:"))
    # Свободный текст — только сообщения, не команды; владельца проверяет
    # `owner_only` внутри хендлера (R-18).
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_free_text)
    )


def build_application(settings: Settings, runtime: Runtime | None = None) -> Application:
    """Собрать приложение. Планировщик стартует в `post_init`, не здесь.

    Так тесты и `--dry-run` могут собрать приложение, ничего не запуская.
    """
    runtime = runtime or build_runtime(settings)
    application = (
        Application.builder().token(settings.bot_token.get_secret_value()).build()
    )
    application.bot_data["workout_service"] = runtime.service
    application.bot_data["wellness_service"] = runtime.wellness
    # Свободный текст: клиент, таймаут и домен-операции — по D1/D7. Клиент не
    # ходит в сеть до первого сообщения, поэтому старт без Ollama безопасен.
    translator = OllamaClient(
        settings.ollama_url,
        settings.ollama_model,
        settings.ollama_timeout_seconds,
    )
    application.bot_data["assistant_service"] = AssistantService(
        translator,
        runtime.service,
        settings.ollama_model,
        settings.ollama_timeout_seconds,
    )
    # Один пользователь — один id. Все входы сверяются с ним (R-05).
    application.bot_data[OWNER_KEY] = settings.owner_id

    async def start_scheduler(app: Application) -> None:
        # Отправители создаются здесь: `Bot` существует только после сборки
        # приложения, а тикам нужен уже готовый транспорт.
        sender = TelegramSender(app.bot, settings.owner_id, settings.zone)
        wellness_sender = TelegramWellnessSender(app.bot, settings.owner_id)
        scheduler = build_scheduler(
            build_tick(runtime, sender, settings),
            wellness_tick=build_wellness_tick(runtime, wellness_sender, settings),
        )
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
