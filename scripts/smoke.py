import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "000000000:test")
os.environ.setdefault("MASTER_TELEGRAM_IDS", "1")
os.environ.setdefault("TIMEZONE", "Europe/Moscow")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/smoke.db"

from config import clear_settings_cache  # noqa: E402
from db.session import ensure_seed, get_session_factory, init_db  # noqa: E402
from services.booking import (  # noqa: E402
    add_slot,
    create_booking,
    days_with_free_slots,
    get_master_settings,
    list_active_services,
    list_free_slots_for_day,
    list_user_upcoming_appointments,
    reschedule_appointment,
)


async def main() -> None:
    db_file = Path("data/smoke.db")
    if db_file.exists():
        db_file.unlink()

    clear_settings_cache()
    await init_db()
    async with get_session_factory()() as session:
        await ensure_seed(session)
        await session.commit()

    tz = ZoneInfo(os.environ["TIMEZONE"])
    start = datetime.now(tz).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=3)
    second = start + timedelta(hours=1)

    async with get_session_factory()() as session:
        await add_slot(session, start)
        await add_slot(session, second)
        await session.commit()

    async with get_session_factory()() as session:
        services = await list_active_services(session)
        assert services, "seed service is missing"
        day_slots = await list_free_slots_for_day(session, start.date())
        assert len(day_slots) == 2, "free slots are missing"
        free_days = await days_with_free_slots(session, start.year, start.month)
        assert start.day in free_days, "calendar does not mark free day"

        appointment = await create_booking(
            session,
            telegram_id=42,
            slot_id=day_slots[0].id,
            service_id=services[0].id,
            contact_name="Test Client",
            contact_phone="+79990000000",
        )
        await session.commit()
        assert appointment.id is not None, "appointment was not created"

    async with get_session_factory()() as session:
        upcoming = await list_user_upcoming_appointments(session, 42)
        assert len(upcoming) == 1, "upcoming appointment is missing"
        free_slots = await list_free_slots_for_day(session, start.date())
        assert len(free_slots) == 1, "booked slot is still free"

        settings = await get_master_settings(session)
        settings.reschedule_deadline_hours = 1
        moved = await reschedule_appointment(
            session,
            appointment_id=upcoming[0].id,
            telegram_id=42,
            new_slot_id=free_slots[0].id,
        )
        await session.commit()
        assert moved.slot_id == free_slots[0].id, "appointment was not rescheduled"

    print("smoke ok")


if __name__ == "__main__":
    asyncio.run(main())
