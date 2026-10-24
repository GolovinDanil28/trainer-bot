"""Тесты подключения к базе: WAL, внешние ключи, идемпотентный create_all."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Column, Integer, String, Table, text

from src.db.base import Base
from src.db.session import create_db_engine, init_db, make_session_factory

# Заглушка до появления настоящих моделей (задача 2.3): без хотя бы одной
# таблицы create_all нечего создавать, и тест идемпотентности был бы пустым.
SMOKE = Table(
    "smoke",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(20), nullable=False),
)


@pytest.fixture
def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'trainer.db'}"


@pytest.fixture
def engine(db_url):
    engine = create_db_engine(db_url)
    yield engine
    engine.dispose()


def test_create_all_creates_missing_table(engine):
    init_db(engine)

    with engine.connect() as conn:
        names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master"))}

    assert "smoke" in names


def test_init_db_is_idempotent(engine):
    init_db(engine)
    init_db(engine)  # не должно падать на уже существующих таблицах

    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM smoke")).scalar()

    assert count == 0


def test_wal_mode_is_on(engine):
    init_db(engine)

    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()

    assert mode.lower() == "wal"


def test_foreign_keys_are_on(engine):
    with engine.connect() as conn:
        enabled = conn.execute(text("PRAGMA foreign_keys")).scalar()

    assert enabled == 1


def test_engine_is_usable_from_another_thread(engine):
    # check_same_thread=False: соединение можно отдать планировщику, который
    # работает не в потоке бота.
    def query() -> int:
        with engine.connect() as conn:
            return conn.execute(text("SELECT 41 + 1")).scalar()

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(query).result() == 42


def test_session_factory_reads_and_writes(engine):
    init_db(engine)
    session = make_session_factory(engine)()

    session.execute(text("INSERT INTO smoke (name) VALUES ('squat')"))
    session.commit()
    name = session.execute(text("SELECT name FROM smoke")).scalar()
    session.close()

    assert name == "squat"


def test_separate_databases_do_not_share_state(tmp_path):
    # Две базы — два независимых файла: тесты не должны видеть данные друг друга.
    first = create_db_engine(f"sqlite:///{tmp_path / 'a.db'}")
    second = create_db_engine(f"sqlite:///{tmp_path / 'b.db'}")
    init_db(first)

    with second.connect() as conn:
        tables = conn.execute(text("SELECT name FROM sqlite_master")).fetchall()
    assert tables == []

    first.dispose()
    second.dispose()
