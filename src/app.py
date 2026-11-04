"""Сборка приложения: база, сервис, хендлеры и планировщик.

Здесь видно, из чего состоит бот, но нет ни одной бизнес-операции: каждая
часть проверяется отдельно, а этот модуль только соединяет их проводами.
"""

from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from telegram import Bot
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.bot.access import OWNER_KEY, REGISTRATION_KEY
from src.bot.assistant_service import AssistantService
from src.bot.callbacks import on_checkin, on_type_choice, on_wellness
from src.bot.handlers import (
    cmd_help,
    cmd_history,
    cmd_list,
    cmd_new,
    cmd_off,
    cmd_plan,
    cmd_settings,
    cmd_start,
    cmd_week,
    cmd_wellness,
    on_free_text,
)
from src.bot.pending import PendingChoices
from src.bot.service import WorkoutService
from src.bot.telegram_sender import (
    TelegramReportSender,
    TelegramSender,
    TelegramWellnessSender,
)
from src.bot.user_service import UserService
from src.bot.wellness_service import WellnessService
from src.config import Settings
from src.core.plan import current_week
from src.core.users import Registration
from src.db.session import create_db_engine, init_db, make_session_factory
from src.llm.ollama import OllamaClient
from src.scheduler.multi import (
    JobFactories,
    MultiUserJobs,
    ReminderJob,
    WeeklyJob,
    WellnessJob,
)
from src.scheduler.runner import build_scheduler


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
    init_db(
        engine,
        settings.owner_id,
        tz=settings.tz,
        wellness_time=settings.wellness_time,
        week_review_time=settings.week_review_time,
    )
    session_factory = make_session_factory(engine)
    return Runtime(
        engine=engine,
        session_factory=session_factory,
        service=WorkoutService(session_factory, settings.zone),
        wellness=WellnessService(session_factory, settings.zone),
    )


def build_all_jobs(runtime: Runtime, bot: Bot, settings: Settings) -> AsyncIOScheduler:
    """Все три job в одном месте: обход пользователей, отправители, зоны."""

    def render_weekly(user, now):  # noqa: ANN001, ANN202
        """Текст разбора собирается в зоне и от имени конкретного человека."""
        zone = ZoneInfo(user.tz)
        start, end = current_week(now.astimezone(zone).date())
        service = WellnessService(
            runtime.session_factory, zone, clock=lambda: now
        )
        return service.weekly_review_for(start, end, now)

    factories = JobFactories(
        reminder_sender=lambda user: TelegramSender(
            bot, user.telegram_id, ZoneInfo(user.tz)
        ),
        wellness_sender=lambda user: TelegramWellnessSender(bot, user.telegram_id),
        report_sender=lambda user: TelegramReportSender(bot, user.telegram_id).send_text,
        render_weekly=render_weekly,
    )
    miss_window = (
        timedelta(hours=settings.miss_window_hours)
        if settings.miss_window_hours is not None
        else None
    )
    jobs = MultiUserJobs(runtime.session_factory, factories, miss_window=miss_window)
    return build_scheduler(
        ReminderJob(jobs),
        wellness_tick=WellnessJob(jobs),
        weekly_tick=WeeklyJob(jobs),
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
    application.add_handler(CommandHandler("week", cmd_week))
    application.add_handler(CommandHandler("plan", cmd_plan))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CallbackQueryHandler(on_checkin, pattern=r"^ci:"))
    application.add_handler(CallbackQueryHandler(on_wellness, pattern=r"^wl:"))
    application.add_handler(
        CallbackQueryHandler(on_type_choice, pattern=r"^tc:")
    )
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
    application.bot_data["session_factory"] = runtime.session_factory
    registration = Registration(
        settings.invite_code,
        settings.tz,
        settings.wellness_time,
        settings.week_review_time,
    )
    application.bot_data[REGISTRATION_KEY] = registration
    application.bot_data["user_service"] = UserService(
        runtime.session_factory, registration
    )
    application.bot_data["workout_service"] = runtime.service
    application.bot_data["wellness_service"] = runtime.wellness
    # Уточнения типа живут в памяти процесса и стареют сами (R-34).
    application.bot_data["pending_choices"] = PendingChoices()
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
        scheduler = build_all_jobs(runtime, app.bot, settings)
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
