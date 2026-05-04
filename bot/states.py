from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    waiting_name = State()
    waiting_phone = State()


class RescheduleStates(StatesGroup):
    choose_slot = State()


class MasterServiceStates(StatesGroup):
    add_name = State()
    add_price = State()
    add_duration = State()
    edit_name = State()
    edit_price = State()
    edit_duration = State()


class MasterSettingsStates(StatesGroup):
    deadline_hours = State()
