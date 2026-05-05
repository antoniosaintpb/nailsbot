from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.common import main_menu_keyboard
from bot.keyboards.calendar import (
    CB_BACK_SLOTS,
    CB_CONFIRM,
    CB_DAY,
    CB_MONTH,
    CB_RESCHED_PICK,
    CB_SLOT,
    CB_SVC,
    month_keyboard,
)
from bot.states import BookingStates, RescheduleStates
from config import get_settings
from services.booking import (
    BookingError,
    RescheduleNotAllowedError,
    SlotTakenError,
    create_booking,
    days_with_free_slots,
    get_master_settings,
    local_dt,
    list_active_services,
    list_free_slots_for_day,
    list_user_upcoming_appointments,
    reschedule_appointment,
)
from services.notify import notify_masters

router = Router(name="client")


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _today() -> date:
    return datetime.now(_tz()).date()


def _max_booking_date() -> date:
    return _today() + timedelta(days=365)


_not_rescheduling = ~StateFilter(RescheduleStates.choose_slot)


async def _booking_calendar_text(session: AsyncSession) -> str:
    settings = await get_master_settings(session)
    rules = "\n".join(f"• {line}" for line in settings.booking_rules.splitlines() if line.strip())
    if not rules:
        rules = "• Правила пока не указаны"
    return (
        "Правила записи:\n"
        f"{rules}\n\n"
        "Выберите день (🟢 — есть свободные окна):"
    )


async def send_booking_calendar(message: Message, session: AsyncSession, ym: str | None = None) -> None:
    today = _today()
    if ym:
        y, m = map(int, ym.split("-"))
    else:
        y, m = today.year, today.month
    free = await days_with_free_slots(session, y, m)
    kb = month_keyboard(
        y,
        m,
        free,
        min_date=today,
        max_date=_max_booking_date(),
    )
    await message.answer(await _booking_calendar_text(session), reply_markup=kb)


async def open_booking_calendar(callback: CallbackQuery, session: AsyncSession, ym: str | None = None) -> None:
    today = _today()
    if ym:
        y, m = map(int, ym.split("-"))
    else:
        y, m = today.year, today.month
    free = await days_with_free_slots(session, y, m)
    kb = month_keyboard(
        y,
        m,
        free,
        min_date=today,
        max_date=_max_booking_date(),
    )
    await callback.message.edit_text(
        await _booking_calendar_text(session),
        reply_markup=kb,
    )


@router.callback_query(_not_rescheduling, F.data.startswith(f"{CB_MONTH}:"))
async def client_cal_month(cq: CallbackQuery, session: AsyncSession) -> None:
    ym = cq.data.split(":", 1)[1]
    today = _today()
    y, m = map(int, ym.split("-"))
    free = await days_with_free_slots(session, y, m)
    kb = month_keyboard(
        y,
        m,
        free,
        min_date=today,
        max_date=_max_booking_date(),
    )
    await cq.message.edit_reply_markup(reply_markup=kb)
    await cq.answer()


@router.callback_query(_not_rescheduling, F.data.startswith(f"{CB_DAY}:"))
async def client_cal_day(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    d = date.fromisoformat(cq.data.split(":", 1)[1])
    today = _today()
    if d < today or d > _max_booking_date():
        await cq.answer("Дата недоступна", show_alert=True)
        return
    slots = await list_free_slots_for_day(session, d)
    if not slots:
        await cq.answer("Нет свободных слотов", show_alert=True)
        return
    await state.update_data(booking_day=d.isoformat())
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, sl in enumerate(slots):
        lt = local_dt(sl.starts_at).strftime("%H:%M")
        row.append(InlineKeyboardButton(text=lt, callback_data=f"{CB_SLOT}:{sl.id}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« Месяц", callback_data=f"{CB_MONTH}:{d.year:04d}-{d.month:02d}")])
    await cq.message.edit_text(
        f"Дата {d.strftime('%d.%m.%Y')}. Выберите время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cq.answer()


@router.callback_query(_not_rescheduling, F.data.startswith(f"{CB_BACK_SLOTS}:"))
async def client_back_to_slots(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    d = date.fromisoformat(cq.data.split(":", 1)[1])
    slots = await list_free_slots_for_day(session, d)
    if not slots:
        await open_booking_calendar(cq, session, f"{d.year:04d}-{d.month:02d}")
        await cq.answer()
        return
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for sl in slots:
        lt = local_dt(sl.starts_at).strftime("%H:%M")
        row.append(InlineKeyboardButton(text=lt, callback_data=f"{CB_SLOT}:{sl.id}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« Месяц", callback_data=f"{CB_MONTH}:{d.year:04d}-{d.month:02d}")])
    await cq.message.edit_text(
        f"Дата {d.strftime('%d.%m.%Y')}. Выберите время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cq.answer()


@router.callback_query(_not_rescheduling, F.data.startswith(f"{CB_SLOT}:"))
async def client_pick_slot(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    sid = int(cq.data.split(":", 1)[1])
    services = await list_active_services(session)
    if not services:
        await cq.answer("У мастера пока нет услуг", show_alert=True)
        return
    data = await state.get_data()
    day_iso = data.get("booking_day") or _today().isoformat()
    rows: list[list[InlineKeyboardButton]] = []
    for sv in services:
        label = f"{sv.name} — {int(sv.price)} ₽ ({sv.duration_min} мин)"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{CB_SVC}:{sv.id}")])
    rows.append(
        [InlineKeyboardButton(text="« Ко времени", callback_data=f"{CB_BACK_SLOTS}:{day_iso}")]
    )
    await state.update_data(slot_id=sid)
    await cq.message.edit_text("Выберите услугу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


@router.callback_query(_not_rescheduling, F.data.startswith(f"{CB_SVC}:"))
async def client_pick_service(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    svc_id = int(cq.data.split(":", 1)[1])
    data = await state.get_data()
    if "slot_id" not in data:
        await cq.answer("Сначала выберите время", show_alert=True)
        return
    await state.update_data(service_id=svc_id)
    await state.set_state(BookingStates.waiting_name)
    await cq.message.edit_text("Введите имя для записи (одним сообщением):")
    await cq.answer()


@router.message(BookingStates.waiting_name, F.text)
async def booking_enter_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Имя слишком короткое, напишите ещё раз.")
        return
    await state.update_data(contact_name=name)
    await state.set_state(BookingStates.waiting_phone)
    await message.answer("Введите телефон (номер или @telegram):")


@router.message(BookingStates.waiting_phone, F.text)
async def booking_enter_phone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    if len(phone) < 5:
        await message.answer("Проверьте телефон и отправьте ещё раз.")
        return
    await state.update_data(contact_phone=phone)
    await message.answer(
        "Подтвердите запись:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Да, записать", callback_data=f"{CB_CONFIRM}:y"),
                    InlineKeyboardButton(text="Отмена", callback_data=f"{CB_CONFIRM}:n"),
                ]
            ]
        ),
    )


@router.callback_query(BookingStates.waiting_phone, F.data.startswith(f"{CB_CONFIRM}:"))
async def booking_confirm(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    act = cq.data.split(":", 1)[1]
    if act == "n":
        await state.clear()
        settings = get_settings()
        is_m = settings.is_master(cq.from_user.id)
        await cq.message.edit_text("Запись отменена.", reply_markup=main_menu_keyboard(is_m))
        await cq.answer()
        return
    data = await state.get_data()
    uid = cq.from_user.id
    try:
        appt = await create_booking(
            session,
            telegram_id=uid,
            slot_id=int(data["slot_id"]),
            service_id=int(data["service_id"]),
            contact_name=str(data["contact_name"]),
            contact_phone=str(data["contact_phone"]),
        )
        slot = appt.slot
        when = local_dt(slot.starts_at).strftime("%d.%m.%Y %H:%M")
        tg_line = (
            f"Telegram: @{cq.from_user.username}"
            if cq.from_user and cq.from_user.username
            else f"Telegram id: {uid}"
        )
        await notify_masters(
            cq.bot,
            "Новая запись\n"
            f"{when} — {appt.service_name_snapshot}\n"
            f"Клиент: {appt.contact_name}, {appt.contact_phone}\n"
            f"Цена: {int(appt.price_snapshot)} ₽\n"
            f"{tg_line}",
        )
        await state.clear()
        settings = get_settings()
        is_m = settings.is_master(uid)
        await cq.message.edit_text(
            f"Вы записаны на {when}. До встречи!",
            reply_markup=main_menu_keyboard(is_m),
        )
    except SlotTakenError:
        await cq.answer("Это время только что заняли. Выберите другое.", show_alert=True)
        await state.clear()
        await open_booking_calendar(cq, session)
        return
    except BookingError as e:
        await cq.answer(str(e), show_alert=True)
        return
    await cq.answer()


async def show_my_appointments(callback: CallbackQuery, session: AsyncSession) -> None:
    uid = callback.from_user.id
    items = await list_user_upcoming_appointments(session, uid)
    if not items:
        settings = get_settings()
        is_m = settings.is_master(uid)
        await callback.message.edit_text(
            "У вас нет предстоящих записей.",
            reply_markup=main_menu_keyboard(is_m),
        )
        return
    rows: list[list[InlineKeyboardButton]] = []
    for a in items:
        when = local_dt(a.slot.starts_at).strftime("%d.%m %H:%M")
        label = f"{when} — {a.service_name_snapshot}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{CB_RESCHED_PICK}:{a.id}")])
    rows.append([InlineKeyboardButton(text="« Меню", callback_data="mn:home")])
    await callback.message.edit_text(
        "Ваши записи. Нажмите, чтобы перенести на другое время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def send_my_appointments(message: Message, session: AsyncSession) -> None:
    uid = message.from_user.id
    items = await list_user_upcoming_appointments(session, uid)
    if not items:
        await message.answer("У вас нет предстоящих записей.")
        return
    rows: list[list[InlineKeyboardButton]] = []
    for a in items:
        when = local_dt(a.slot.starts_at).strftime("%d.%m %H:%M")
        label = f"{when} — {a.service_name_snapshot}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{CB_RESCHED_PICK}:{a.id}")])
    await message.answer(
        "Ваши записи. Нажмите, чтобы перенести на другое время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith(f"{CB_RESCHED_PICK}:"))
async def reschedule_start(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    appt_id = int(cq.data.split(":", 1)[1])
    await state.set_state(RescheduleStates.choose_slot)
    await state.update_data(reschedule_appt_id=appt_id)
    today = _today()
    y, m = today.year, today.month
    free = await days_with_free_slots(session, y, m)
    kb = month_keyboard(
        y,
        m,
        free,
        min_date=today,
        max_date=_max_booking_date(),
    )
    await cq.message.edit_text(
        "Перенос записи. Выберите новый день (🟢 — есть свободные окна):",
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(StateFilter(RescheduleStates.choose_slot), F.data.startswith(f"{CB_MONTH}:"))
async def resched_cal_month(cq: CallbackQuery, session: AsyncSession) -> None:
    ym = cq.data.split(":", 1)[1]
    today = _today()
    y, m = map(int, ym.split("-"))
    free = await days_with_free_slots(session, y, m)
    kb = month_keyboard(
        y,
        m,
        free,
        min_date=today,
        max_date=_max_booking_date(),
    )
    await cq.message.edit_reply_markup(reply_markup=kb)
    await cq.answer()


@router.callback_query(StateFilter(RescheduleStates.choose_slot), F.data.startswith(f"{CB_DAY}:"))
async def resched_cal_day(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    d = date.fromisoformat(cq.data.split(":", 1)[1])
    today = _today()
    if d < today or d > _max_booking_date():
        await cq.answer("Дата недоступна", show_alert=True)
        return
    slots = await list_free_slots_for_day(session, d)
    if not slots:
        await cq.answer("Нет свободных слотов", show_alert=True)
        return
    await state.update_data(reschedule_day=d.isoformat())
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for sl in slots:
        lt = local_dt(sl.starts_at).strftime("%H:%M")
        row.append(InlineKeyboardButton(text=lt, callback_data=f"{CB_SLOT}:r:{sl.id}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« Месяц", callback_data=f"{CB_MONTH}:{d.year:04d}-{d.month:02d}")])
    await cq.message.edit_text(
        f"Новая дата {d.strftime('%d.%m.%Y')}. Выберите время:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await cq.answer()


@router.callback_query(StateFilter(RescheduleStates.choose_slot), F.data.startswith(f"{CB_SLOT}:r:"))
async def resched_pick_slot(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    new_sid = int(cq.data.split(":", 2)[2])
    data = await state.get_data()
    appt_id = int(data["reschedule_appt_id"])
    uid = cq.from_user.id
    try:
        appt = await reschedule_appointment(
            session,
            appointment_id=appt_id,
            telegram_id=uid,
            new_slot_id=new_sid,
        )
        slot = appt.slot
        when = local_dt(slot.starts_at).strftime("%d.%m.%Y %H:%M")
        await notify_masters(
            cq.bot,
            "Перенос записи\n"
            f"Новое время: {when}\n"
            f"{appt.service_name_snapshot}\n"
            f"{appt.contact_name}, {appt.contact_phone}",
        )
        await state.clear()
        settings = get_settings()
        is_m = settings.is_master(uid)
        await cq.message.edit_text(
            f"Запись перенесена на {when}.",
            reply_markup=main_menu_keyboard(is_m),
        )
    except RescheduleNotAllowedError:
        await cq.answer(
            "Перенос недоступен: до начала услуги осталось меньше допустимого срока.",
            show_alert=True,
        )
        return
    except SlotTakenError:
        await cq.answer("Слот занят, выберите другое время.", show_alert=True)
        return
    except BookingError as e:
        await cq.answer(str(e), show_alert=True)
        return
    await cq.answer()


