"""Подключение к базе: движок, фабрика сессий, создание таблиц.

Здесь нет ни одной бизнес-операции: модуль умеет только открыть базу
и убедиться, что таблицы существуют.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

# Модели обязаны быть импортированы до create_all: метадата знает только те
# таблицы, чьи классы уже загружены.
from src.db import models  # noqa: F401
from src.db.base import Base
from src.db.context import DEFAULT_USER_ID


def create_db_engine(db_url: str) -> Engine:
    """Создать движок по строке подключения."""
    is_sqlite = make_url(db_url).get_backend_name() == "sqlite"
    if not is_sqlite:
        return create_engine(db_url)

    engine = create_engine(
        db_url,
        # Планировщик и хендлеры бота работают в разных потоках, а sqlite по
        # умолчанию запрещает использовать соединение не в том потоке, где его
        # создали. Запрет не имеет смысла при коротких транзакциях.
        connect_args={"check_same_thread": False},
    )
    _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine: Engine) -> None:
    """Включить настройки, которые SQLite не включает сам."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        # WAL: читатели не блокируют писателя — иначе тик планировщика
        # и команды бота начнут ронять друг друга на коротких транзакциях.
        cursor.execute("PRAGMA journal_mode=WAL")
        # Внешние ключи в SQLite выключены по умолчанию: без этого ON DELETE
        # молча не сработает, и в базе останутся сироты.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db(
    engine: Engine,
    owner_telegram_id: int | None = None,
    *,
    tz: str = "UTC",
    wellness_time: str = "21:00",
    week_review_time: str = "20:00",
) -> None:
    """Создать таблицы, перевести старую схему на пользователей, завести владельца.

    Порядок важен: миграция должна увидеть старые данные до `create_all`.
    Если владелец не передан (тесты), создаётся пользователь по умолчанию с
    `id = DEFAULT_USER_ID`: на него опираются операции по умолчанию.
    Идемпотентно: повторный запуск ничего не ломает.
    """
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "v1 поддерживает SQLite: миграции и уникальные ключи рассчитаны на него"
        )
    if _needs_multiuser_migration(engine):
        _migrate_to_multiuser(engine, owner_telegram_id, tz, wellness_time, week_review_time)
    Base.metadata.create_all(engine)
    apply_migrations(engine)
    ensure_owner(engine, owner_telegram_id, tz, wellness_time, week_review_time)


# Порядок удаления: сначала дети, потом родители — иначе SQLite с включёнными
# внешними ключами откажется удалять таблицу, на которую есть ссылки.
_DROP_ORDER = (
    "checkin",
    "reminder",
    "schedule",
    "plan_item",
    "plan_snapshot",
    "weekly_report",
    "wellness",
    "workout",
)
_OWNED_TABLES = ("workout", "wellness", "plan_snapshot", "weekly_report")
_INSERT_ORDER = (
    "workout",
    "schedule",
    "reminder",
    "checkin",
    "wellness",
    "plan_snapshot",
    "plan_item",
    "weekly_report",
)


def _now() -> datetime:
    """Момент создания пользователя: raw SQL идёт мимо Python-дефолтов."""
    return datetime.now(UTC).replace(tzinfo=None)


def _columns(connection, table: str) -> list[str]:
    return [row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")]


def _needs_multiuser_migration(engine: Engine) -> bool:
    """Старая база узнаётся по `workout` без `user_id` (R-41)."""
    if engine.dialect.name != "sqlite":
        return False
    with engine.connect() as connection:
        columns = _columns(connection, "workout")
    return bool(columns) and "user_id" not in columns


def _migrate_to_multiuser(
    engine: Engine,
    owner_telegram_id: int | None,
    tz: str,
    wellness_time: str,
    week_review_time: str,
) -> None:
    """Перенести данные старой базы на владельца, пересобрав таблицы.

    Одной транзакцией это сделать нельзя: pysqlite коммитит DDL (DROP TABLE)
    независимо от транзакции. Поэтому перед миграцией снимается копия файла
    базы, а после вставки сверяется число строк каждой таблицы. Любой сбой —
    восстановление копии и исключение: история тренировок невосстановима, и
    «пустой бот с молчаливой потерей» недопустим.
    """
    backup = _backup_database(engine)
    try:
        _run_multiuser_migration(
            engine, owner_telegram_id, tz, wellness_time, week_review_time
        )
    except Exception:
        _restore_database(engine, backup)
        raise


def _backup_database(engine: Engine) -> Path | None:
    """Копия файла базы рядом с ним. `None` — база не файловая."""
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    source = Path(database)
    if not source.exists():
        return None
    backup = source.with_name(f"{source.name}.backup-{datetime.now(UTC):%Y%m%d%H%M%S}")
    shutil.copy2(source, backup)
    return backup


def _restore_database(engine: Engine, backup: Path | None) -> None:
    """Вернуть копию на место. Движок обязан отпустить файл до копирования."""
    engine.dispose()
    if backup is None:
        return
    shutil.copy2(backup, Path(engine.url.database or ""))
    backup.unlink(missing_ok=True)


def _run_multiuser_migration(
    engine: Engine,
    owner_telegram_id: int | None,
    tz: str,
    wellness_time: str,
    week_review_time: str,
) -> None:
    with engine.begin() as connection:
        existing = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        legacy: dict[str, list[dict[str, object]]] = {}
        for table in _INSERT_ORDER:
            if table in existing:
                legacy[table] = [
                    dict(row._mapping)
                    for row in connection.exec_driver_sql(f"SELECT * FROM {table}")
                ]

        for table in _DROP_ORDER:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")

        Base.metadata.create_all(connection)
        owner_id = _ensure_owner_row(
            connection, owner_telegram_id, tz, wellness_time, week_review_time
        )

        for table in _INSERT_ORDER:
            rows = legacy.get(table, [])
            if not rows:
                continue
            allowed = set(_columns(connection, table))
            for row in rows:
                values = {key: value for key, value in row.items() if key in allowed}
                if table in _OWNED_TABLES:
                    values["user_id"] = owner_id
                if table == "weekly_report" and values.get("claimed_at") is None:
                    # Старая строка без claimed_at (колонка появилась позже или
                    # была NULL) — считаем попытку сделанной в момент отправки.
                    values["claimed_at"] = values.get("sent_at") or _now()
                columns = ", ".join(values)
                placeholders = ", ".join(f":{key}" for key in values)
                connection.exec_driver_sql(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", values
                )

        # Сверка: миграция обязана сохранить каждую строку (R-43).
        for table, rows in legacy.items():
            actual = connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table}"
            ).scalar()
            if actual != len(rows):
                raise RuntimeError(
                    f"миграция потеряла строки в {table}: {len(rows)} -> {actual}"
                )


def ensure_owner(
    engine: Engine,
    telegram_id: int | None,
    tz: str,
    wellness_time: str,
    week_review_time: str,
) -> None:
    """Завести владельца и пользователя по умолчанию. Существующих не трогаем."""
    with engine.begin() as connection:
        _ensure_owner_row(connection, telegram_id, tz, wellness_time, week_review_time)


def _ensure_owner_row(connection, telegram_id, tz, wellness_time, week_review_time) -> int:
    """Вернуть id владельца, создав его при необходимости.

    Без явного владельца (тесты) создаётся пользователь по умолчанию с
    `id = DEFAULT_USER_ID`, чтобы операции «по умолчанию» имели владельца.
    """
    if telegram_id is None:
        connection.exec_driver_sql(
            "INSERT INTO user (id, telegram_id, tz, wellness_time, week_review_time, "
            "is_active, created_at) VALUES (?, 0, ?, ?, ?, 1, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (DEFAULT_USER_ID, tz, wellness_time, week_review_time, _now()),
        )
        return DEFAULT_USER_ID

    connection.exec_driver_sql(
        "INSERT INTO user (telegram_id, tz, wellness_time, week_review_time, "
        "is_active, created_at) VALUES (?, ?, ?, ?, 1, ?) "
        "ON CONFLICT(telegram_id) DO NOTHING",
        (telegram_id, tz, wellness_time, week_review_time, _now()),
    )
    row = connection.exec_driver_sql(
        "SELECT id FROM user WHERE telegram_id = ?", (telegram_id,)
    ).first()
    assert row is not None
    return int(row[0])


# Колонки, добавленные после первого релиза. `create_all` умеет создавать
# только отсутствующие таблицы, поэтому старые базы надо догонять руками.
# Список данных, а не механизм версий: две добавки — это не фреймворк (§8.2).
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (таблица, колонка, тип SQL)
    ("workout", "distance_km", "FLOAT"),
    ("workout", "date_once", "DATE"),
    ("workout", "time_once", "TEXT"),
    ("weekly_report", "claimed_at", "DATETIME"),
)


def apply_migrations(engine: Engine) -> None:
    """Добавить недостающие колонки. Повторный вызов безопасен."""
    if engine.dialect.name != "sqlite":
        # Схема v1 живёт на SQLite; для других движков миграции делались бы
        # инструментом, а не pragma-проверкой.
        return
    with engine.begin() as connection:
        for table, column, sql_type in _ADDED_COLUMNS:
            existing = {
                row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if not existing:
                # Таблицы ещё нет: её создаст create_all целиком, ALTER не нужен.
                continue
            if column not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Фабрика сессий: один вызов — одна короткая транзакция."""
    return sessionmaker(bind=engine, expire_on_commit=False)
