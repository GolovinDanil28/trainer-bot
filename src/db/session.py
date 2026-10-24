"""Подключение к базе: движок, фабрика сессий, создание таблиц.

Здесь нет ни одной бизнес-операции: модуль умеет только открыть базу
и убедиться, что таблицы существуют.
"""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

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
    """Создать отсутствующие таблицы. Идемпотентно: повторный вызов безопасен."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Фабрика сессий: один вызов — одна короткая транзакция."""
    return sessionmaker(bind=engine, expire_on_commit=False)
