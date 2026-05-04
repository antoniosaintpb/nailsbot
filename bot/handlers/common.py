from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from services.booking import get_or_create_user

router = Router(name="common")


def main_menu_keyboard(is_master: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Записаться", callback_data="mn:b")],
        [InlineKeyboardButton(text="Мои записи", callback_data="mn:m")],
    ]
    if is_master:
        rows.append([InlineKeyboardButton(text="Мастер", callback_data="mn:x")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    settings = get_settings()
    uid = message.from_user.id if message.from_user else 0
    is_m = settings.is_master(uid)
    await get_or_create_user(session, uid, is_master=is_m)
    await message.answer(
        "Маникюр — запись через бота.\nВыберите действие:",
        reply_markup=main_menu_keyboard(is_m),
    )


@router.callback_query(F.data == "mn:b")
async def menu_book(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    from bot.handlers.client import open_booking_calendar

    await state.clear()
    await open_booking_calendar(callback, session)
    await callback.answer()


@router.callback_query(F.data == "mn:m")
async def menu_my(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    from bot.handlers.client import show_my_appointments

    await state.clear()
    await show_my_appointments(callback, session)
    await callback.answer()


@router.callback_query(F.data == "mn:x")
async def menu_master(callback: CallbackQuery) -> None:
    from bot.handlers.master import open_master_menu

    if not get_settings().is_master(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await open_master_menu(callback)
    await callback.answer()


@router.callback_query(F.data == "mn:home")
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    settings = get_settings()
    uid = callback.from_user.id if callback.from_user else 0
    is_m = settings.is_master(uid)
    await callback.message.edit_text(
        "Главное меню.",
        reply_markup=main_menu_keyboard(is_m),
    )
    await callback.answer()
