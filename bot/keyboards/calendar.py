import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Client callback prefixes
CB_MONTH = "cm"
CB_DAY = "cd"
CB_SLOT = "cs"
CB_SVC = "cv"
CB_CONFIRM = "cf"
# Master calendar prefixes (separate from client)
MB_MONTH = "mm"
MB_DAY = "md"
CB_IGNORE = "ci"
CB_BACK_SLOTS = "cb"  # cb:YYYY-MM-DD вернуться к выбору времени
CB_RESCHED_PICK = "rp"  # rp:appointment_id


def _month_label(year: int, month: int) -> str:
    months = (
        "Янв",
        "Фев",
        "Мар",
        "Апр",
        "Май",
        "Июн",
        "Июл",
        "Авг",
        "Сен",
        "Окт",
        "Ноя",
        "Дек",
    )
    return f"{months[month - 1]} {year}"


def month_keyboard(
    year: int,
    month: int,
    free_days: set[int],
    *,
    min_date: date | None = None,
    max_date: date | None = None,
    month_callback_prefix: str = CB_MONTH,
    day_callback_prefix: str = CB_DAY,
) -> InlineKeyboardMarkup:
    """Calendar for one month. Days outside [min_date, max_date] are placeholders."""
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(year, month)
    rows: list[list[InlineKeyboardButton]] = []

    ym_prev = _shift_month(year, month, -1)
    ym_next = _shift_month(year, month, 1)
    nav_row = [
        InlineKeyboardButton(text="◀", callback_data=f"{month_callback_prefix}:{ym_prev}"),
        InlineKeyboardButton(text=_month_label(year, month), callback_data=f"{CB_IGNORE}:0"),
        InlineKeyboardButton(text="▶", callback_data=f"{month_callback_prefix}:{ym_next}"),
    ]
    rows.append(nav_row)

    wd_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    rows.append([InlineKeyboardButton(text=n, callback_data=f"{CB_IGNORE}:h") for n in wd_names])

    for week in weeks:
        row_buttons: list[InlineKeyboardButton] = []
        for d in week:
            if d.month != month:
                row_buttons.append(InlineKeyboardButton(text=" ", callback_data=f"{CB_IGNORE}:x"))
                continue
            in_range = True
            if min_date is not None and d < min_date:
                in_range = False
            if max_date is not None and d > max_date:
                in_range = False
            label = str(d.day)
            if in_range and d.day in free_days:
                label = f"🟢{d.day}"
            if not in_range:
                row_buttons.append(InlineKeyboardButton(text=f"·{d.day}", callback_data=f"{CB_IGNORE}:p"))
            else:
                row_buttons.append(
                    InlineKeyboardButton(
                        text=label,
                        callback_data=f"{day_callback_prefix}:{d.isoformat()}",
                    )
                )
        rows.append(row_buttons)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shift_month(year: int, month: int, delta: int) -> str:
    m = month + delta
    y = year
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"


def time_picker_keyboard(
    day_iso: str,
    prefix: str,
    *,
    times_labels: list[tuple[str, str]],
    month_callback_prefix: str = CB_MONTH,
) -> InlineKeyboardMarkup:
    """times_labels: list of (button_text, callback_suffix). callback = prefix + ':' + suffix"""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, (text, suffix) in enumerate(times_labels):
        row.append(InlineKeyboardButton(text=text, callback_data=f"{prefix}:{suffix}"))
        if len(row) == 4 or i == len(times_labels) - 1:
            rows.append(row)
            row = []
    rows.append(
        [InlineKeyboardButton(text="« Месяц", callback_data=f"{month_callback_prefix}:{day_iso[:7]}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
