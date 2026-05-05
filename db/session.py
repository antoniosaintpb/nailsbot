from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from config import get_settings
from db.base import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _ensure_sqlite_parent(settings.database_url)
        _engine = create_async_engine(settings.database_url, echo=False)
    return _engine


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite+aiosqlite:///"):
        return
    path_part = database_url.removeprefix("sqlite+aiosqlite:///")
    if path_part in ("", "/"):
        return
    db_path = Path(unquote(path_part))
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if get_settings().database_url.startswith("sqlite+aiosqlite:///"):
            await _ensure_sqlite_schema(conn)


async def _ensure_sqlite_schema(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(master_settings)"))
    existing = {row[1] for row in result.fetchall()}
    columns = {
        "workday_start_hour": "INTEGER NOT NULL DEFAULT 9",
        "workday_end_hour": "INTEGER NOT NULL DEFAULT 21",
        "slot_step_min": "INTEGER NOT NULL DEFAULT 30",
        "booking_rules": (
            "TEXT NOT NULL DEFAULT 'Не приходите раньше чем за 30 минут до записи\n"
            "Если не можете прийти — предупредите минимум за 24 часа\n"
            "Принимаем клиентов от 13 лет\n"
            "При опоздании более 15 минут запись может быть отменена'"
        ),
    }
    for name, definition in columns.items():
        if name not in existing:
            await conn.execute(text(f"ALTER TABLE master_settings ADD COLUMN {name} {definition}"))


async def ensure_seed(session: AsyncSession) -> None:
    from sqlalchemy import func, select

    from db.models import MasterSettings, Service

    r = await session.execute(select(MasterSettings).limit(1))
    if r.scalar_one_or_none() is None:
        session.add(
            MasterSettings(
                reschedule_deadline_hours=24,
                default_slot_duration_min=60,
                workday_start_hour=9,
                workday_end_hour=21,
                slot_step_min=30,
            )
        )

    n = await session.scalar(select(func.count()).select_from(Service))
    if n == 0:
        session.add(
            Service(
                name="Маникюр классический",
                duration_min=90,
                price=2500,
                is_active=True,
                sort_order=0,
            )
        )
