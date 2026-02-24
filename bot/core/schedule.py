import re
from datetime import date, datetime
from calendar import monthrange

from bot.config import EMPLOYEES, ANCHOR_DATE, MSK, NAMES


def is_work_day(name: str, target_date: date) -> bool:
    if name not in EMPLOYEES:
        return False
    delta = (target_date - ANCHOR_DATE).days
    pos = (EMPLOYEES[name]["anchor_pos"] + delta) % 4
    return pos in (0, 1)


def get_shift(name: str, target_date: date) -> str:
    if is_work_day(name, target_date):
        return EMPLOYEES[name]["shift"]
    return "Выходной"


def generate_month_updates(month_num: str, year: int) -> list:
    updates = []
    days_in_month = monthrange(year, int(month_num))[1]
    for day in range(1, days_in_month + 1):
        d = date(year, int(month_num), day)
        day_z = str(day).zfill(2)
        for name in NAMES:
            updates.append({
                "name": name,
                "date": f"{day_z}.{month_num}",
                "time": get_shift(name, d),
            })
    return updates


def col_index_to_letter(idx: int) -> str:
    result = ""
    while idx >= 0:
        result = chr(idx % 26 + ord("A")) + result
        idx = idx // 26 - 1
    return result


def validate_time(time_str: str) -> bool:
    if time_str == "Выходной":
        return True
    return bool(re.match(r"^\d{2}:\d{2} - \d{2}:\d{2}$", time_str))


def build_date_targets(day_z: str, month_z: str, year: int) -> list[str]:
    return [
        f"{day_z}.{month_z}.{year}",
        f"{day_z}.{month_z}",
        f"{year}-{month_z}-{day_z}",
        f"{day_z}/{month_z}/{year}",
    ]


def find_date_row(all_values: list, day_z: str, month_z: str, year: int) -> int | None:
    targets = build_date_targets(day_z, month_z, year)
    for i, row in enumerate(all_values):
        if not row or not row[0].strip():
            continue
        cell = row[0].strip()
        for target in targets:
            if target in cell:
                return i
    return None


def find_name_col(header_row: list, name: str) -> int | None:
    for j, cell in enumerate(header_row):
        if cell.strip() == name:
            return j
    return None


def find_row_and_col(
    all_values: list, day: int, month_num: str, name: str, year: int | None = None
) -> tuple[int | None, int | None]:
    if year is None:
        year = datetime.now(MSK).year
    day_z = str(day).zfill(2)
    month_z = month_num.zfill(2)
    row_index = find_date_row(all_values, day_z, month_z, year)
    col_index = None
    if len(all_values) > 1:
        col_index = find_name_col(all_values[1], name)
    return row_index, col_index
