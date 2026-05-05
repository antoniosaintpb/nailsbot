import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    pass


class UserRole(str, enum.Enum):
    client = "client"
    master = "master"


class SlotStatus(str, enum.Enum):
    free = "free"
    booked = "booked"
    blocked = "blocked"


class AppointmentStatus(str, enum.Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"
    rescheduled = "rescheduled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False), default=UserRole.client)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="user")


class MasterSettings(Base):
    __tablename__ = "master_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reschedule_deadline_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    default_slot_duration_min: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    workday_start_hour: Mapped[int] = mapped_column(Integer, default=9, nullable=False)
    workday_end_hour: Mapped[int] = mapped_column(Integer, default=21, nullable=False)
    slot_step_min: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    booking_rules: Mapped[str] = mapped_column(
        Text,
        default=(
            "Не приходите раньше чем за 30 минут до записи\n"
            "Если не можете прийти — предупредите минимум за 24 часа\n"
            "Принимаем клиентов от 13 лет\n"
            "При опоздании более 15 минут запись может быть отменена"
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="service")


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SlotStatus] = mapped_column(Enum(SlotStatus, native_enum=False), default=SlotStatus.free, nullable=False)

    appointment: Mapped["Appointment | None"] = relationship(
        back_populates="slot",
        uselist=False,
    )

    __table_args__ = (UniqueConstraint("starts_at", name="uq_availability_slots_starts_at"),)


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("availability_slots.id"), nullable=False, unique=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(64), nullable=False)
    price_snapshot: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    service_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, native_enum=False), default=AppointmentStatus.confirmed, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="appointments")
    slot: Mapped["AvailabilitySlot"] = relationship(back_populates="appointment")
    service: Mapped["Service"] = relationship(back_populates="appointments")
