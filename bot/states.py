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
    default_duration = State()
    work_start_hour = State()
    work_end_hour = State()
    slot_step = State()
    booking_rules = State()
