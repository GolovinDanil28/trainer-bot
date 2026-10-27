"""Сборка приложения: обработчики и запуск планировщика (4.5, R-10).

Telegram не поднимается: приложение инициализируется без сети, а тик внутри
планировщика использует настоящую SQLite во временной папке.
"""

from pathlib import Path

from telegram.ext import CommandHandler, MessageHandler

from src.app import build_application
from src.bot.handlers import on_free_text
from src.config import Settings
from src.core.help import COMMANDS
from src.scheduler.runner import JOB_ID, WELLNESS_JOB_ID


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token="123:TEST",  # формат валиден, в сеть не ходим
        owner_id=42,
        tz="UTC",
        db_url=f"sqlite:///{tmp_path / 'app.db'}",
    )


def test_application_registers_commands(tmp_path):
    application = build_application(make_settings(tmp_path))

    commands = {
        command
        for group in application.handlers.values()
        for handler in group
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }

    assert commands == {
        "start",
        "help",
        "new",
        "list",
        "off",
        "history",
        "wellness",
        "plan",
    }


def test_help_lists_exactly_the_registered_commands(tmp_path):
    # Гарантия «справка не отстаёт от команд»: один источник — COMMANDS,
    # а регистрация в приложении обязана с ним совпадать.
    application = build_application(make_settings(tmp_path))

    registered = {
        command
        for group in application.handlers.values()
        for handler in group
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    documented = {entry.command.lstrip("/") for entry in COMMANDS}

    assert registered == documented

    # Команду можно спрятать в MessageHandler по регулярке — тогда проверка выше
    # её не увидит. Единственный MessageHandler обязан быть свободным текстом.
    message_handlers = [
        handler
        for group in application.handlers.values()
        for handler in group
        if isinstance(handler, MessageHandler)
    ]
    assert [handler.callback for handler in message_handlers] == [on_free_text]


def test_application_registers_free_text_once_and_builds_assistant(tmp_path):
    application = build_application(make_settings(tmp_path))

    free_text = [
        handler
        for group in application.handlers.values()
        for handler in group
        if isinstance(handler, MessageHandler)
    ]

    assert len(free_text) == 1
    assert free_text[0].callback is on_free_text
    assert "assistant_service" in application.bot_data
    assert "workout_service" in application.bot_data
    assert "wellness_service" in application.bot_data


async def test_start_hook_registers_running_job_and_stop_hook_cleans_up(tmp_path):
    application = build_application(make_settings(tmp_path))

    # Вызываем хуки жизненного цикла напрямую: `initialize()` в PTB 22 ходит
    # в сеть за `get_me()`, а старт планировщика от сети не зависит.
    await application.post_init(application)
    try:
        scheduler = application.bot_data["scheduler"]
        assert scheduler.running
        assert scheduler.get_job(JOB_ID) is not None

        # У вечернего вопроса свой job: общий `max_instances` заставил бы два
        # независимых сценария ждать друг друга.
        wellness_job = scheduler.get_job(WELLNESS_JOB_ID)
        assert wellness_job is not None
        assert wellness_job.id != JOB_ID
        assert wellness_job.max_instances == 1
    finally:
        await application.post_shutdown(application)

    # post_shutdown обязан убрать планировщик, иначе процесс не завершится.
    assert "scheduler" not in application.bot_data
