"""Подключение к базе: движок, фабрика сессий, создание таблиц.

Здесь нет ни одной бизнес-операции: модуль умеет только открыть базу
и убедиться, что таблицы существуют.
"""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

# Модели обязаны быть импортированы до create_all: метадата знает только те
# таблицы, чьи классы уже загружены.
from src.db import models  # noqa: F401
from src.db.base import Base


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


def init_db(engine: Engine) -> None:
    """Создать отсутствующие таблицы и догнать схему. Идемпотентно."""
    Base.metadata.create_all(engine)
    apply_migrations(engine)


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
