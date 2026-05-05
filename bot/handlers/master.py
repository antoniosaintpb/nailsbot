from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.common import main_menu_keyboard
from bot.keyboards.calendar import MB_DAY, MB_MONTH, month_keyboard
from bot.states import MasterServiceStates, MasterSettingsStates
from config import get_settings
from db.models import Appointment, AvailabilitySlot, Service, SlotStatus
from services.booking import (
    BookingError,
    add_slot,
    appointment_stats,
    delete_free_slot,
    get_master_settings,
    local_dt,
    list_slots_for_day_master,
)

router = Router(name="master")


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _is_master(uid: int) -> bool:
    return get_settings().is_master(uid)


def master_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Рабочие часы и слоты", callback_data="mx:sl")],
            [InlineKeyboardButton(text="Настройки рабочего дня", callback_data="mx:work")],
            [InlineKeyboardButton(text="Услуги и цены", callback_data="mx:sv")],
            [InlineKeyboardButton(text="Длительность сеансов", callback_data="mx:dur")],
            [InlineKeyboardButton(text="Правила записи", callback_data="mx:rules")],
            [InlineKeyboardButton(text="Срок переноса (часы)", callback_data="mx:st")],
            [InlineKeyboardButton(text="Статистика", callback_data="mx:sc")],
            [InlineKeyboardButton(text="« Главное меню", callback_data="mn:home")],
        ]
    )


async def open_master_menu(cq: CallbackQuery) -> None:
    if not _is_master(cq.from_user.id):
        return
    await cq.message.edit_text("Панель мастера:", reply_markup=master_root_kb())


async def send_master_menu(message: Message) -> None:
    if not _is_master(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer("Панель мастера:", reply_markup=master_root_kb())


@router.callback_query(F.data == "mx:sl")
async def master_slots_entry(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    today = datetime.now(_tz()).date()
    y, m = today.year, today.month
    kb = month_keyboard(
        y,
        m,
        set(),
        min_date=today - timedelta(days=1),
        max_date=today.replace(year=today.year + 2),
        month_callback_prefix=MB_MONTH,
        day_callback_prefix=MB_DAY,
    )
    await cq.message.edit_text(
        "Выберите день для управления слотами.",
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(F.data.startswith(f"{MB_MONTH}:"))
async def master_month(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    ym = cq.data.split(":", 1)[1]
    y, m = map(int, ym.split("-"))
    today = datetime.now(_tz()).date()
    kb = month_keyboard(
        y,
        m,
        set(),
        min_date=today - timedelta(days=1),
        max_date=today.replace(year=today.year + 2),
        month_callback_prefix=MB_MONTH,
        day_callback_prefix=MB_DAY,
    )
    await cq.message.edit_reply_markup(reply_markup=kb)
    await cq.answer()


def _time_add_buttons(d: date, *, start_hour: int, end_hour: int, step_min: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    start_min = start_hour * 60
    end_min = end_hour * 60
    t = start_min
    while t < end_min:
        h, mm = divmod(t, 60)
        suf = f"{d.year:04d}{d.month:02d}{d.day:02d}{h:02d}{mm:02d}"
        row.append(InlineKeyboardButton(text=f"{h:02d}:{mm:02d}", callback_data=f"ma:{suf}"))
        if len(row) == 4:
            rows.append(row)
            row = []
        t += step_min
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« К дню", callback_data=f"{MB_DAY}:{d.isoformat()}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith(f"{MB_DAY}:"))
async def master_day(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    d = date.fromisoformat(cq.data.split(":", 1)[1])
    slots = await list_slots_for_day_master(session, d)
    tz = _tz()
    lines = [f"День {d.strftime('%d.%m.%Y')}"]
    rows: list[list[InlineKeyboardButton]] = []
    for s in slots:
        tstr = local_dt(s.starts_at).strftime("%H:%M")
        mark = "🟢" if s.status == SlotStatus.free else "🔒"
        lines.append(f"{mark} {tstr} ({s.duration_min} мин)")
        if s.status == SlotStatus.free:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Удалить {tstr}",
                        callback_data=f"dl:{s.id}",
                    )
                ]
            )
    text = "\n".join(lines)
    rows.append([InlineKeyboardButton(text="+ Добавить время", callback_data=f"mt:{d.isoformat()}")])
    rows.append([InlineKeyboardButton(text="« Месяц", callback_data=f"{MB_MONTH}:{d.year:04d}-{d.month:02d}")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


@router.callback_query(F.data.startswith("mt:"))
async def master_time_grid(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    d = date.fromisoformat(cq.data.split(":", 1)[1])
    settings = await get_master_settings(session)
    await cq.message.edit_text(
        f"Время для {d.strftime('%d.%m.%Y')}\n"
        f"Рабочие часы: {settings.workday_start_hour}:00–{settings.workday_end_hour}:00\n"
        f"Шаг: {settings.slot_step_min} мин, длительность сеанса: {settings.default_slot_duration_min} мин",
        reply_markup=_time_add_buttons(
            d,
            start_hour=settings.workday_start_hour,
            end_hour=settings.workday_end_hour,
            step_min=settings.slot_step_min,
        ),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("ma:"))
async def master_add_slot(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    suf = cq.data.split(":", 1)[1]
    tz = _tz()
    try:
        y, mo, da, h, mi = int(suf[0:4]), int(suf[4:6]), int(suf[6:8]), int(suf[8:10]), int(suf[10:12])
        starts = datetime(y, mo, da, h, mi, tzinfo=tz)
    except (ValueError, IndexError):
        await cq.answer("Неверный формат", show_alert=True)
        return
    try:
        await add_slot(session, starts)
    except BookingError as e:
        await cq.answer(str(e), show_alert=True)
        return
    except IntegrityError:
        await session.rollback()
        await cq.answer("Слот на это время уже есть.", show_alert=True)
        return
    d = local_dt(starts).date()
    slots = await list_slots_for_day_master(session, d)
    lines = [f"День {d.strftime('%d.%m.%Y')}"]
    rows: list[list[InlineKeyboardButton]] = []
    for s in slots:
        tstr = local_dt(s.starts_at).strftime("%H:%M")
        mark = "🟢" if s.status == SlotStatus.free else "🔒"
        lines.append(f"{mark} {tstr} ({s.duration_min} мин)")
        if s.status == SlotStatus.free:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Удалить {tstr}",
                        callback_data=f"dl:{s.id}",
                    )
                ]
            )
    text = "\n".join(lines)
    rows.append([InlineKeyboardButton(text="+ Добавить время", callback_data=f"mt:{d.isoformat()}")])
    rows.append([InlineKeyboardButton(text="« Месяц", callback_data=f"{MB_MONTH}:{d.year:04d}-{d.month:02d}")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer("Слот добавлен")


@router.callback_query(F.data.startswith("dl:"))
async def master_del_slot(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    sid = int(cq.data.split(":", 1)[1])
    tz = _tz()
    r0 = await session.execute(select(AvailabilitySlot).where(AvailabilitySlot.id == sid))
    old = r0.scalar_one_or_none()
    d_old = local_dt(old.starts_at).date() if old else None
    try:
        ok = await delete_free_slot(session, sid)
    except BookingError as e:
        await cq.answer(str(e), show_alert=True)
        return
    if not ok:
        await cq.answer("Слот не найден", show_alert=True)
        return
    await cq.answer("Удалено")
    if d_old:
        slots = await list_slots_for_day_master(session, d_old)
        lines = [f"День {d_old.strftime('%d.%m.%Y')}"]
        rows: list[list[InlineKeyboardButton]] = []
        for s in slots:
            tstr = local_dt(s.starts_at).strftime("%H:%M")
            mark = "🟢" if s.status == SlotStatus.free else "🔒"
            lines.append(f"{mark} {tstr} ({s.duration_min} мин)")
            if s.status == SlotStatus.free:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"Удалить {tstr}",
                            callback_data=f"dl:{s.id}",
                        )
                    ]
                )
        text = "\n".join(lines)
        rows.append([InlineKeyboardButton(text="+ Добавить время", callback_data=f"mt:{d_old.isoformat()}")])
        rows.append(
            [InlineKeyboardButton(text="« Месяц", callback_data=f"{MB_MONTH}:{d_old.year:04d}-{d_old.month:02d}")]
        )
        await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await cq.message.edit_text("Слот удалён.", reply_markup=master_root_kb())


@router.callback_query(F.data == "mx:sv")
async def master_services_menu(cq: CallbackQuery, session: AsyncSession, *, skip_answer: bool = False) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    r = await session.execute(select(Service).order_by(Service.sort_order, Service.id))
    svcs = list(r.scalars().all())
    rows: list[list[InlineKeyboardButton]] = []
    for s in svcs:
        mark = "✓" if s.is_active else "✗"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {s.name} — {int(s.price)} ₽",
                    callback_data=f"sv:{s.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Новая услуга", callback_data="sv:new")])
    rows.append([InlineKeyboardButton(text="« Назад", callback_data="mx:back")])
    await cq.message.edit_text("Услуги:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    if not skip_answer:
        await cq.answer()


@router.callback_query(F.data == "mx:back")
async def master_back(cq: CallbackQuery) -> None:
    await open_master_menu(cq)
    await cq.answer()


@router.callback_query(F.data == "mx:work")
async def master_workday_menu(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    settings = await get_master_settings(session)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начало рабочего дня", callback_data="wk:start")],
            [InlineKeyboardButton(text="Конец рабочего дня", callback_data="wk:end")],
            [InlineKeyboardButton(text="Шаг выбора времени", callback_data="wk:step")],
            [InlineKeyboardButton(text="« Назад", callback_data="mx:back")],
        ]
    )
    await cq.message.edit_text(
        "Настройки рабочего дня:\n"
        f"Начало: {settings.workday_start_hour}:00\n"
        f"Конец: {settings.workday_end_hour}:00\n"
        f"Шаг времени: {settings.slot_step_min} мин",
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(F.data.startswith("wk:"))
async def master_workday_field(cq: CallbackQuery, state: FSMContext) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    field = cq.data.split(":", 1)[1]
    if field == "start":
        await state.set_state(MasterSettingsStates.work_start_hour)
        await cq.message.edit_text("Введите час начала рабочего дня (0–23), например 10:")
    elif field == "end":
        await state.set_state(MasterSettingsStates.work_end_hour)
        await cq.message.edit_text("Введите час конца рабочего дня (1–24), например 21:")
    elif field == "step":
        await state.set_state(MasterSettingsStates.slot_step)
        await cq.message.edit_text("Введите шаг выбора времени в минутах: 15, 30 или 60:")
    await cq.answer()


@router.message(MasterSettingsStates.work_start_hour, F.text)
async def master_set_work_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 0 <= int(raw) <= 23:
        await message.answer("Введите целый час от 0 до 23.")
        return
    settings = await get_master_settings(session)
    value = int(raw)
    if value >= settings.workday_end_hour:
        await message.answer("Начало должно быть раньше конца рабочего дня.")
        return
    settings.workday_start_hour = value
    await state.clear()
    await message.answer(f"Начало рабочего дня: {value}:00")


@router.message(MasterSettingsStates.work_end_hour, F.text)
async def master_set_work_end(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 24:
        await message.answer("Введите целый час от 1 до 24.")
        return
    settings = await get_master_settings(session)
    value = int(raw)
    if value <= settings.workday_start_hour:
        await message.answer("Конец должен быть позже начала рабочего дня.")
        return
    settings.workday_end_hour = value
    await state.clear()
    await message.answer(f"Конец рабочего дня: {value}:00")


@router.message(MasterSettingsStates.slot_step, F.text)
async def master_set_slot_step(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if raw not in {"15", "30", "60"}:
        await message.answer("Введите 15, 30 или 60.")
        return
    settings = await get_master_settings(session)
    settings.slot_step_min = int(raw)
    await state.clear()
    await message.answer(f"Шаг выбора времени: {raw} мин.")


@router.callback_query(F.data == "mx:dur")
async def master_duration_menu(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    settings = await get_master_settings(session)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить длительность", callback_data="du:set")],
            [InlineKeyboardButton(text="« Назад", callback_data="mx:back")],
        ]
    )
    await cq.message.edit_text(
        f"Текущая длительность новых сеансов: {settings.default_slot_duration_min} мин.\n"
        "Она применяется к новым слотам. Для услуг длительность можно менять отдельно в «Услуги и цены».",
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(F.data == "du:set")
async def master_duration_custom(cq: CallbackQuery, state: FSMContext) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    await state.set_state(MasterSettingsStates.default_duration)
    await cq.message.edit_text("Введите длительность новых сеансов в минутах, например 60, 90 или 120:")
    await cq.answer()


@router.message(MasterSettingsStates.default_duration, F.text)
async def master_duration_set(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 5 or int(raw) > 600:
        await message.answer("Введите число минут от 5 до 600.")
        return
    settings = await get_master_settings(session)
    settings.default_slot_duration_min = int(raw)
    await state.clear()
    await message.answer(f"Длительность новых сеансов: {raw} мин.")


@router.callback_query(F.data == "mx:rules")
async def master_rules_menu(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    settings = await get_master_settings(session)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить правила", callback_data="ru:set")],
            [InlineKeyboardButton(text="« Назад", callback_data="mx:back")],
        ]
    )
    await cq.message.edit_text(
        "Текущие правила записи:\n\n" + settings.booking_rules,
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(F.data == "ru:set")
async def master_rules_custom(cq: CallbackQuery, state: FSMContext) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    await state.set_state(MasterSettingsStates.booking_rules)
    await cq.message.edit_text(
        "Отправьте правила одним сообщением.\n"
        "Каждое правило лучше писать с новой строки."
    )
    await cq.answer()


@router.message(MasterSettingsStates.booking_rules, F.text)
async def master_rules_set(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Правила слишком короткие.")
        return
    settings = await get_master_settings(session)
    settings.booking_rules = text
    await state.clear()
    await message.answer("Правила записи обновлены.")


@router.callback_query(F.data.startswith("sv:"))
async def master_service_open(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    part = cq.data.split(":", 1)[1]
    if part == "new":
        await state.set_state(MasterServiceStates.add_name)
        await cq.message.edit_text("Название новой услуги:")
        await cq.answer()
        return
    sid = int(part)
    r = await session.execute(select(Service).where(Service.id == sid))
    s = r.scalar_one_or_none()
    if s is None:
        await cq.answer("Не найдено", show_alert=True)
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить название", callback_data=f"sf:n:{sid}")],
            [InlineKeyboardButton(text="Изменить цену (₽)", callback_data=f"sf:p:{sid}")],
            [InlineKeyboardButton(text="Изменить длительность (мин)", callback_data=f"sf:d:{sid}")],
            [InlineKeyboardButton(text="Вкл/выкл", callback_data=f"sf:t:{sid}")],
            [InlineKeyboardButton(text="Удалить", callback_data=f"sf:x:{sid}")],
            [InlineKeyboardButton(text="« Список", callback_data="mx:sv")],
        ]
    )
    await cq.message.edit_text(
        f"{s.name}\nЦена: {int(s.price)} ₽\nДлительность: {s.duration_min} мин\nАктивна: {'да' if s.is_active else 'нет'}",
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(F.data.startswith("sf:"))
async def master_service_field(cq: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    _, field, sid_s = cq.data.split(":")
    sid = int(sid_s)
    r = await session.execute(select(Service).where(Service.id == sid))
    s = r.scalar_one_or_none()
    if s is None:
        await cq.answer("Не найдено", show_alert=True)
        return
    if field == "t":
        s.is_active = not s.is_active
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Изменить название", callback_data=f"sf:n:{sid}")],
                [InlineKeyboardButton(text="Изменить цену (₽)", callback_data=f"sf:p:{sid}")],
                [InlineKeyboardButton(text="Изменить длительность (мин)", callback_data=f"sf:d:{sid}")],
                [InlineKeyboardButton(text="Вкл/выкл", callback_data=f"sf:t:{sid}")],
                [InlineKeyboardButton(text="Удалить", callback_data=f"sf:x:{sid}")],
                [InlineKeyboardButton(text="« Список", callback_data="mx:sv")],
            ]
        )
        await cq.message.edit_text(
            f"{s.name}\nЦена: {int(s.price)} ₽\nДлительность: {s.duration_min} мин\nАктивна: {'да' if s.is_active else 'нет'}",
            reply_markup=kb,
        )
        await cq.answer("Сохранено")
        return
    if field == "x":
        cnt = await session.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.service_id == sid)
        )
        if cnt and cnt > 0:
            s.is_active = False
            await cq.answer("Есть записи — услуга деактивирована", show_alert=True)
        else:
            await session.delete(s)
            await cq.answer("Удалено")
        await master_services_menu(cq, session, skip_answer=True)
        return
    await state.update_data(edit_service_id=sid)
    if field == "n":
        await state.set_state(MasterServiceStates.edit_name)
        await cq.message.edit_text("Новое название:")
    elif field == "p":
        await state.set_state(MasterServiceStates.edit_price)
        await cq.message.edit_text("Новая цена (целое число, ₽):")
    elif field == "d":
        await state.set_state(MasterServiceStates.edit_duration)
        await cq.message.edit_text("Новая длительность (минуты, число):")
    await cq.answer()


@router.message(MasterServiceStates.add_name, F.text)
async def master_add_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Слишком коротко")
        return
    await state.update_data(svc_name=name)
    await state.set_state(MasterServiceStates.add_price)
    await message.answer("Цена (₽, целое число):")


@router.message(MasterServiceStates.add_price, F.text)
async def master_add_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("Введите целое число")
        return
    await state.update_data(svc_price=int(raw))
    await state.set_state(MasterServiceStates.add_duration)
    await message.answer("Длительность (минуты):")


@router.message(MasterServiceStates.add_duration, F.text)
async def master_add_duration(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 5:
        await message.answer("Введите число минут (не меньше 5)")
        return
    data = await state.get_data()
    svc = Service(
        name=str(data["svc_name"]),
        price=Decimal(str(data["svc_price"])),
        duration_min=int(raw),
        is_active=True,
        sort_order=0,
    )
    session.add(svc)
    await state.clear()
    await message.answer("Услуга добавлена.")


@router.message(MasterServiceStates.edit_name, F.text)
async def master_edit_name(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    data = await state.get_data()
    sid = int(data["edit_service_id"])
    r = await session.execute(select(Service).where(Service.id == sid))
    s = r.scalar_one_or_none()
    if s:
        s.name = (message.text or "").strip()
    await state.clear()
    await message.answer("Название обновлено.")


@router.message(MasterServiceStates.edit_price, F.text)
async def master_edit_price(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer("Введите целое число")
        return
    data = await state.get_data()
    sid = int(data["edit_service_id"])
    r = await session.execute(select(Service).where(Service.id == sid))
    s = r.scalar_one_or_none()
    if s:
        s.price = Decimal(str(int(raw)))
    await state.clear()
    await message.answer("Цена обновлена.")


@router.message(MasterServiceStates.edit_duration, F.text)
async def master_edit_duration(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 5:
        await message.answer("Введите число минут")
        return
    data = await state.get_data()
    sid = int(data["edit_service_id"])
    r = await session.execute(select(Service).where(Service.id == sid))
    s = r.scalar_one_or_none()
    if s:
        s.duration_min = int(raw)
    await state.clear()
    await message.answer("Длительность обновлена.")


@router.callback_query(F.data == "mx:st")
async def master_settings_menu(
    cq: CallbackQuery, session: AsyncSession, *, skip_answer: bool = False
) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    ms = await get_master_settings(session)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="-1 ч", callback_data="sd:m"),
                InlineKeyboardButton(text="+1 ч", callback_data="sd:p"),
            ],
            [InlineKeyboardButton(text="Задать числом", callback_data="sd:s")],
            [InlineKeyboardButton(text="« Назад", callback_data="mx:back")],
        ]
    )
    await cq.message.edit_text(
        f"Минимум часов до начала услуги, когда ещё можно перенести запись: **{ms.reschedule_deadline_hours}** ч.",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    if not skip_answer:
        await cq.answer()


@router.callback_query(F.data == "sd:m")
async def master_deadline_minus(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    ms = await get_master_settings(session)
    ms.reschedule_deadline_hours = max(1, ms.reschedule_deadline_hours - 1)
    await master_settings_menu(cq, session, skip_answer=True)
    await cq.answer()


@router.callback_query(F.data == "sd:p")
async def master_deadline_plus(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    ms = await get_master_settings(session)
    ms.reschedule_deadline_hours = min(168, ms.reschedule_deadline_hours + 1)
    await master_settings_menu(cq, session, skip_answer=True)
    await cq.answer()


@router.callback_query(F.data == "sd:s")
async def master_deadline_custom(cq: CallbackQuery, state: FSMContext) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    await state.set_state(MasterSettingsStates.deadline_hours)
    await cq.message.edit_text("Введите целое число часов (1–168):")
    await cq.answer()


@router.message(MasterSettingsStates.deadline_hours, F.text)
async def master_deadline_set(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not _is_master(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужно целое число")
        return
    v = int(raw)
    if v < 1 or v > 168:
        await message.answer("От 1 до 168")
        return
    ms = await get_master_settings(session)
    ms.reschedule_deadline_hours = v
    await state.clear()
    await message.answer(f"Срок переноса: {v} ч до начала услуги.")


@router.callback_query(F.data == "mx:sc")
async def master_stats(cq: CallbackQuery, session: AsyncSession) -> None:
    if not _is_master(cq.from_user.id):
        await cq.answer()
        return
    tz = _tz()
    now = datetime.now(tz)
    start_w = now - timedelta(days=7)
    start_m = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    st_w = await appointment_stats(session, start_w, now + timedelta(days=1))
    st_m = await appointment_stats(session, start_m, now + timedelta(days=1))
    text = (
        "**Статистика**\n"
        f"7 дней: записей {st_w.count}, сумма {int(st_w.revenue)} ₽, отмен {st_w.cancelled}\n"
        f"Месяц: записей {st_m.count}, сумма {int(st_m.revenue)} ₽, отмен {st_m.cancelled}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« Назад", callback_data="mx:back")]]
    )
    await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await cq.answer()
