from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import get_settings
from db.models import (
    Appointment,
    AppointmentStatus,
    AvailabilitySlot,
    MasterSettings,
    Service,
    SlotStatus,
    User,
    UserRole,
)


class BookingError(Exception):
    pass


class SlotTakenError(BookingError):
    pass


class RescheduleNotAllowedError(BookingError):
    pass


@dataclass
class DayInfo:
    day: int
    has_free: bool


async def get_or_create_user(session: AsyncSession, telegram_id: int, *, is_master: bool) -> User:
    r = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = r.scalar_one_or_none()
    if user:
        want = UserRole.master if is_master else UserRole.client
        if user.role != want and is_master:
            user.role = UserRole.master
        return user
    user = User(telegram_id=telegram_id, role=UserRole.master if is_master else UserRole.client)
    session.add(user)
    await session.flush()
    return user


async def get_master_settings(session: AsyncSession) -> MasterSettings:
    r = await session.execute(select(MasterSettings).limit(1))
    row = r.scalar_one_or_none()
    if row is None:
        row = MasterSettings(reschedule_deadline_hours=24, default_slot_duration_min=60)
        session.add(row)
        await session.flush()
    return row


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def local_dt(value: datetime) -> datetime:
    """Return datetime in bot timezone without shifting naive SQLite values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=_tz())
    return value.astimezone(_tz())


def db_dt(value: datetime) -> datetime:
    """Store/query local wall-clock time in SQLite without timezone offset surprises."""
    return local_dt(value).replace(tzinfo=None)


def month_range_local(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0)
    return start, end


async def days_with_free_slots(session: AsyncSession, year: int, month: int) -> set[int]:
    start, end = month_range_local(year, month)
    r2 = await session.execute(
        select(AvailabilitySlot.starts_at).where(
            and_(
                AvailabilitySlot.starts_at >= start,
                AvailabilitySlot.starts_at < end,
                AvailabilitySlot.status == SlotStatus.free,
            )
        )
    )
    days: set[int] = set()
    for (st,) in r2.all():
        local = local_dt(st)
        days.add(local.day)
    return days


async def list_free_slots_for_day(session: AsyncSession, d: date) -> list[AvailabilitySlot]:
    start = datetime(d.year, d.month, d.day, 0, 0, 0)
    end = start + timedelta(days=1)
    r = await session.execute(
        select(AvailabilitySlot)
        .where(
            and_(
                AvailabilitySlot.starts_at >= start,
                AvailabilitySlot.starts_at < end,
                AvailabilitySlot.status == SlotStatus.free,
            )
        )
        .order_by(AvailabilitySlot.starts_at)
    )
    return list(r.scalars().all())


async def list_active_services(session: AsyncSession) -> list[Service]:
    r = await session.execute(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.sort_order, Service.id)
    )
    return list(r.scalars().all())


async def create_booking(
    session: AsyncSession,
    *,
    telegram_id: int,
    slot_id: int,
    service_id: int,
    contact_name: str,
    contact_phone: str,
) -> Appointment:
    settings = get_settings()
    is_master = settings.is_master(telegram_id)
    user = await get_or_create_user(session, telegram_id, is_master=is_master)

    slot_r = await session.execute(
        select(AvailabilitySlot).where(AvailabilitySlot.id == slot_id).with_for_update()
    )
    slot = slot_r.scalar_one_or_none()
    if slot is None or slot.status != SlotStatus.free:
        raise SlotTakenError()

    svc_r = await session.execute(select(Service).where(Service.id == service_id, Service.is_active.is_(True)))
    svc = svc_r.scalar_one_or_none()
    if svc is None:
        raise BookingError("Услуга недоступна")

    appt = Appointment(
        user_id=user.id,
        slot_id=slot.id,
        service_id=svc.id,
        contact_name=contact_name.strip(),
        contact_phone=contact_phone.strip(),
        price_snapshot=Decimal(str(svc.price)),
        service_name_snapshot=svc.name,
        status=AppointmentStatus.confirmed,
    )
    session.add(appt)
    slot.status = SlotStatus.booked
    await session.flush()
    await session.refresh(appt, attribute_names=["slot", "service", "user"])
    return appt


async def list_user_upcoming_appointments(session: AsyncSession, telegram_id: int) -> list[Appointment]:
    now = db_dt(datetime.now(_tz()))
    r = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = r.scalar_one_or_none()
    if user is None:
        return []
    r2 = await session.execute(
        select(Appointment)
        .join(AvailabilitySlot, Appointment.slot_id == AvailabilitySlot.id)
        .where(
            Appointment.user_id == user.id,
            Appointment.status == AppointmentStatus.confirmed,
            AvailabilitySlot.starts_at >= now,
        )
        .order_by(AvailabilitySlot.starts_at)
        .options(selectinload(Appointment.slot), selectinload(Appointment.service))
    )
    return list(r2.scalars().unique().all())


async def reschedule_appointment(
    session: AsyncSession,
    *,
    appointment_id: int,
    telegram_id: int,
    new_slot_id: int,
) -> Appointment:
    ms = await get_master_settings(session)
    deadline_h = ms.reschedule_deadline_hours
    tz = _tz()
    now = datetime.now(tz)

    settings = get_settings()
    await get_or_create_user(session, telegram_id, is_master=settings.is_master(telegram_id))

    r = await session.execute(select(User).where(User.telegram_id == telegram_id))
    u = r.scalar_one_or_none()
    if u is None:
        raise BookingError("Пользователь не найден")

    ap_r = await session.execute(
        select(Appointment)
        .where(Appointment.id == appointment_id, Appointment.user_id == u.id)
        .options(selectinload(Appointment.slot))
    )
    appt = ap_r.scalar_one_or_none()
    if appt is None or appt.status != AppointmentStatus.confirmed:
        raise BookingError("Запись не найдена")

    old_slot = appt.slot
    if old_slot is None:
        raise BookingError("Слот не найден")

    cutoff = local_dt(old_slot.starts_at) - timedelta(hours=deadline_h)
    if now >= cutoff:
        raise RescheduleNotAllowedError()

    new_r = await session.execute(
        select(AvailabilitySlot).where(AvailabilitySlot.id == new_slot_id).with_for_update()
    )
    new_slot = new_r.scalar_one_or_none()
    if new_slot is None or new_slot.status != SlotStatus.free:
        raise SlotTakenError()

    old_slot.status = SlotStatus.free
    new_slot.status = SlotStatus.booked
    appt.slot_id = new_slot.id
    await session.flush()
    await session.refresh(appt, attribute_names=["slot", "service", "user"])
    return appt


async def add_slot(session: AsyncSession, starts_at: datetime, duration_min: int | None = None) -> AvailabilitySlot:
    ms = await get_master_settings(session)
    d = duration_min if duration_min is not None else ms.default_slot_duration_min
    starts_at = db_dt(starts_at)
    slot = AvailabilitySlot(starts_at=starts_at, duration_min=d, status=SlotStatus.free)
    session.add(slot)
    try:
        await session.flush()
    except Exception as e:
        raise BookingError("Не удалось добавить слот (возможно дубликат времени)") from e
    return slot


async def delete_free_slot(session: AsyncSession, slot_id: int) -> bool:
    r = await session.execute(select(AvailabilitySlot).where(AvailabilitySlot.id == slot_id).with_for_update())
    slot = r.scalar_one_or_none()
    if slot is None:
        return False
    if slot.status != SlotStatus.free:
        raise BookingError("Нельзя удалить занятый слот")
    await session.delete(slot)
    return True


async def list_slots_for_day_master(session: AsyncSession, d: date) -> list[AvailabilitySlot]:
    start = datetime(d.year, d.month, d.day, 0, 0, 0)
    end = start + timedelta(days=1)
    r = await session.execute(
        select(AvailabilitySlot)
        .where(
            and_(
                AvailabilitySlot.starts_at >= start,
                AvailabilitySlot.starts_at < end,
            )
        )
        .order_by(AvailabilitySlot.starts_at)
    )
    return list(r.scalars().all())


@dataclass
class StatsResult:
    count: int
    revenue: Decimal
    cancelled: int


async def appointment_stats(session: AsyncSession, start: datetime, end: datetime) -> StatsResult:
    r_conf = await session.execute(
        select(func.count(Appointment.id), func.coalesce(func.sum(Appointment.price_snapshot), 0)).where(
            and_(
                Appointment.created_at >= start,
                Appointment.created_at < end,
                Appointment.status.in_([AppointmentStatus.confirmed, AppointmentStatus.rescheduled]),
            )
        )
    )
    row = r_conf.one()
    cnt = int(row[0] or 0)
    rev = Decimal(str(row[1] or 0))

    r_can = await session.execute(
        select(func.count(Appointment.id)).where(
            and_(
                Appointment.created_at >= start,
                Appointment.created_at < end,
                Appointment.status == AppointmentStatus.cancelled,
            )
        )
    )
    cancelled = int(r_can.scalar_one() or 0)
    return StatsResult(count=cnt, revenue=rev, cancelled=cancelled)
