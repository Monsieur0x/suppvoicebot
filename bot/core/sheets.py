import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor

import gspread
from google.oauth2.service_account import Credentials

from bot.config import (
    SPREADSHEET_ID, SERVICE_ACCOUNT_PATH, CACHE_TTL, GC_CHECK_INTERVAL,
    MONTHS_SHEETS, MONTHS_RU, MSK, NAMES, DAYS_RU, logger,
)
from bot.core.schedule import (
    validate_time, col_index_to_letter, find_row_and_col, find_date_row,
)
from bot.state import (
    sheets_cache, sheets_cache_time, history,
    save_history_entry, delete_history_entry, invalidate_cache,
)

# ===================== КЛИЕНТ =====================

_gc = None
_gc_last_check = 0.0
_executor = ThreadPoolExecutor(max_workers=2)


def _get_gspread_client():
    global _gc, _gc_last_check
    now = time.time()
    if _gc and now - _gc_last_check < GC_CHECK_INTERVAL:
        return _gc
    try:
        logger.info("Переподключаюсь к Google Sheets...")
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_PATH,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _gc = gspread.authorize(creds)
        _gc_last_check = now
        logger.info("Подключился к Google Sheets")
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        raise
    return _gc


def shutdown_executor():
    _executor.shutdown(wait=False)


async def run_in_executor(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, func, *args)


# ===================== СИНХРОННЫЕ ОПЕРАЦИИ =====================


def _get_worksheet(month: str):
    sheet_name = MONTHS_SHEETS.get(month)
    if not sheet_name:
        raise ValueError(f"Нет листа для месяца {month}")
    for attempt in range(5):
        try:
            return _get_gspread_client().open_by_key(SPREADSHEET_ID).worksheet(sheet_name), sheet_name
        except Exception as e:
            if "429" in str(e) and attempt < 4:
                wait = 2 ** attempt
                logger.warning(f"429 от Sheets, жду {wait}с (попытка {attempt + 1}/5)...")
                time.sleep(wait)
            else:
                raise


def _get_sheet_data(month: str):
    now = time.time()
    if month in sheets_cache and now - sheets_cache_time.get(month, 0) < CACHE_TTL:
        return sheets_cache[month], None
    try:
        ws, _ = _get_worksheet(month)
        data = ws.get_all_values()
        sheets_cache[month] = data
        sheets_cache_time[month] = now
        return data, ws
    except Exception as e:
        return None, str(e)


def _update_sheet(name: str, date_str: str, new_time: str, is_undo: bool = False) -> str:
    try:
        parts = date_str.split(".")
        day = int(parts[0])
        month_num = parts[1]
        year = datetime.now(MSK).year
        day_z = str(day).zfill(2)
        month_z = month_num.zfill(2)

        if not is_undo and not validate_time(new_time):
            return f"❌ Некорректный формат времени: {new_time}"

        ws, _ = _get_worksheet(month_num)
        all_values = ws.get_all_values()
        row_index, col_index = find_row_and_col(all_values, day, month_num, name, year)

        if row_index is None:
            return f"❌ Не нашёл дату '{day_z}.{month_z}.{year}'"
        if col_index is None:
            return f"❌ Не нашёл имя '{name}'"

        history_key = f"{name}_{date_str}"

        if is_undo:
            if history_key in history:
                entry = history[history_key]
                old_value = entry["old"] if isinstance(entry, dict) else entry
                ws.update_cell(row_index + 1, col_index + 1, old_value)
                invalidate_cache(month_num)
                delete_history_entry(history_key)
                return f"↩️ Восстановлено! {name} / {day_z}.{month_z}.{year} → {old_value}"
            return f"❌ Нет сохранённого значения для {name} / {day_z}.{month_z}.{year}"

        current_value = all_values[row_index][col_index]
        save_history_entry(history_key, current_value, new_time)
        ws.update_cell(row_index + 1, col_index + 1, new_time)
        invalidate_cache(month_num)
        return f"✅ {name} / {day_z}.{month_z} → {new_time} _(было: {current_value})_"

    except Exception as e:
        logger.error(f"Ошибка в update_sheet: {e}", exc_info=True)
        return f"❌ Ошибка: {e}"


def _batch_update_sheet(updates: list) -> list:
    by_month: dict[str, list] = defaultdict(list)
    for u in updates:
        month_num = u["date"].split(".")[1]
        by_month[month_num].append(u)

    results = []
    for month_num, month_updates in by_month.items():
        try:
            ws, _ = _get_worksheet(month_num)
            all_values = ws.get_all_values()
            year = datetime.now(MSK).year
            batch = []
            for u in month_updates:
                name = u["name"]
                date_str = u["date"]
                new_time = u["time"]
                day = int(date_str.split(".")[0])
                day_z = str(day).zfill(2)
                month_z = month_num.zfill(2)

                if not validate_time(new_time):
                    results.append(f"❌ Некорректный формат для {name}: {new_time}")
                    continue

                row_index, col_index = find_row_and_col(all_values, day, month_num, name, year)
                if row_index is None:
                    results.append(f"❌ Не нашёл дату '{day_z}.{month_z}' для {name}")
                    continue
                if col_index is None:
                    results.append(f"❌ Не нашёл имя '{name}'")
                    continue

                current_value = all_values[row_index][col_index]
                save_history_entry(f"{name}_{date_str}", current_value, new_time)
                col_letter = col_index_to_letter(col_index)
                batch.append({"range": f"{col_letter}{row_index + 1}", "values": [[new_time]]})
                results.append(f"✅ {name} / {day_z}.{month_z} → {new_time} _(было: {current_value})_")

            if batch:
                ws.batch_update(batch)
                invalidate_cache(month_num)
                logger.info(f"Батч: {len(batch)} ячеек в месяце {month_num}")
        except Exception as e:
            logger.error(f"Ошибка batch_update месяц {month_num}: {e}", exc_info=True)
            results.append(f"❌ Ошибка для месяца {month_num}: {e}")
    return results


def _create_month_sheet(month_num: str, year: int):
    sheet_name = MONTHS_SHEETS.get(month_num)
    if not sheet_name:
        raise ValueError(f"Нет названия листа для месяца {month_num}")
    month_name_ru = MONTHS_RU.get(month_num, sheet_name)
    days_in_month = monthrange(year, int(month_num))[1]

    spreadsheet = _get_gspread_client().open_by_key(SPREADSHEET_ID)
    ws = spreadsheet.add_worksheet(title=sheet_name, rows=days_in_month + 5, cols=len(NAMES) + 1)
    logger.info(f"Создан лист: {sheet_name}")

    header1 = [""] + [month_name_ru] + [""] * (len(NAMES) - 1)
    header2 = [""] + NAMES
    ws.update("A1", [header1, header2])

    date_col = [[f"{str(day).zfill(2)}.{month_num}.{year}"] for day in range(1, days_in_month + 1)]
    ws.update("A3", date_col)
    invalidate_cache(month_num)
    logger.info(f"Лист {sheet_name} создан: {days_in_month} дней")
    return ws


def _execute_fill_sync(updates: list, month_num: str) -> tuple[int, list[str]]:
    by_month: dict[str, list] = defaultdict(list)
    for u in updates:
        by_month[u["date"].split(".")[1]].append(u)

    total_ok = 0
    total_err = []

    for mn, month_updates in by_month.items():
        try:
            ws, _ = _get_worksheet(mn)
            all_values = ws.get_all_values()
            if len(all_values) < 2:
                total_err.append(f"❌ Лист {mn} пустой — нет заголовков")
                continue

            header = all_values[1]
            col_map = {cell.strip(): j for j, cell in enumerate(header) if cell.strip()}
            row_map = {}
            for i, row in enumerate(all_values):
                if not row or not row[0].strip():
                    continue
                parts = row[0].strip().replace("/", ".").split(".")
                if len(parts) >= 2:
                    row_map[f"{parts[0].zfill(2)}.{parts[1].zfill(2)}"] = i

            batch = []
            for u in month_updates:
                name = u["name"]
                day_z, month_z = u["date"].split(".")
                new_time = u["time"]
                short_key = f"{day_z}.{month_z}"
                row_index = row_map.get(short_key)
                col_index = col_map.get(name)
                if row_index is None:
                    total_err.append(f"❌ Дата {short_key} не найдена для {name}")
                    continue
                if col_index is None:
                    total_err.append(f"❌ Столбец {name} не найден")
                    continue
                col_letter = col_index_to_letter(col_index)
                batch.append({"range": f"{col_letter}{row_index + 1}", "values": [[new_time]]})
                total_ok += 1

            if batch:
                ws.batch_update(batch)
                invalidate_cache(mn)
                logger.info(f"fill: {len(batch)} ячеек в {mn}")

        except Exception as e:
            logger.error(f"Ошибка fill {mn}: {e}", exc_info=True)
            total_err.append(f"❌ Ошибка месяца {mn}: {e}")

    return total_ok, total_err


def _get_current_snapshot_sync() -> dict:
    result = {}
    today = datetime.now(MSK)
    prev_month = (today.replace(day=1) - timedelta(days=1)).strftime("%m")
    curr_month = today.strftime("%m")
    next_month = (today.replace(day=1) + timedelta(days=32)).strftime("%m")
    for month_num in {prev_month, curr_month, next_month}:
        try:
            ws, _ = _get_worksheet(month_num)
            all_values = ws.get_all_values()
            if len(all_values) < 2:
                continue
            headers = all_values[1]
            for row in all_values[2:]:
                if not row or not row[0].strip():
                    continue
                date_key = row[0].strip()
                for col_idx, name in enumerate(headers):
                    if not name.strip() or col_idx >= len(row):
                        continue
                    result[f"{name.strip()}_{date_key}"] = row[col_idx].strip()
        except Exception as e:
            logger.error(f"Ошибка снимка месяц {month_num}: {e}")
    return result


def _get_schedule_for_period_sync(date_from_str: str, date_to_str: str):
    year = datetime.now(MSK).year
    try:
        d1 = datetime.strptime(f"{date_from_str}.{year}", "%d.%m.%Y")
        d2 = datetime.strptime(f"{date_to_str}.{year}", "%d.%m.%Y")
    except ValueError:
        return None, None, "❌ Неверный формат дат"
    if d2 < d1:
        return None, None, "❌ Конечная дата раньше начальной"
    if (d2 - d1).days > 31:
        return None, None, "❌ Период не может быть больше 31 дня"

    month_cache = {}
    all_rows = []
    all_headers = None
    current = d1
    while current <= d2:
        month_num = current.strftime("%m")
        day_z = current.strftime("%d")
        if month_num not in month_cache:
            try:
                ws, _ = _get_worksheet(month_num)
                month_cache[month_num] = ws.get_all_values()
            except Exception as e:
                logger.error(f"Не удалось загрузить лист {month_num}: {e}")
                month_cache[month_num] = None
        if month_cache[month_num] is None:
            current += timedelta(days=1)
            continue
        all_values = month_cache[month_num]
        if all_headers is None:
            all_headers = [c.strip() for c in all_values[1][1:] if c.strip()]

        row_index = find_date_row(all_values, day_z, month_num.zfill(2), year)
        if row_index is not None:
            row_data = all_values[row_index]
            values = [row_data[j].strip() if j < len(row_data) else "—" for j in range(1, len(all_headers) + 1)]
            all_rows.append({
                "date": f"{day_z}.{month_num}",
                "day": DAYS_RU[current.weekday()],
                "values": values,
            })
        current += timedelta(days=1)

    if not all_rows:
        return None, None, "❌ Не нашёл данные за этот период"

    title = (
        f"Расписание на {date_from_str}.{year}"
        if d1 == d2
        else f"Расписание {date_from_str} — {date_to_str}.{year}"
    )
    return title, all_headers, all_rows


def _check_worksheet_exists(month_num: str) -> bool:
    try:
        _get_worksheet(month_num)
        return True
    except Exception:
        return False


def _get_workers_for_date_sync(date_str: str) -> tuple[list, list, str | None]:
    parts = date_str.split(".")
    day = int(parts[0])
    month_num = parts[1]
    year = datetime.now(MSK).year
    day_z = str(day).zfill(2)
    month_z = month_num.zfill(2)

    all_values, err = _get_sheet_data(month_num)
    if all_values is None:
        return [], [], f"❌ Не удалось загрузить данные: {err}"

    row_index = find_date_row(all_values, day_z, month_z, year)
    if row_index is None:
        return [], [], f"❌ Не нашёл дату '{day_z}.{month_z}.{year}'"

    header_row = all_values[1]
    data_row = all_values[row_index]
    workers, off = [], []
    for j in range(1, len(header_row)):
        n = header_row[j].strip()
        v = data_row[j].strip() if j < len(data_row) else "—"
        if n:
            if v == "Выходной":
                off.append(n)
            else:
                workers.append(f"🕐 {n}: {v}")
    return workers, off, None


# ===================== ASYNC ОБЁРТКИ =====================


async def update_sheet(name: str, date_str: str, new_time: str, is_undo: bool = False) -> str:
    return await run_in_executor(_update_sheet, name, date_str, new_time, is_undo)

async def batch_update_sheet(updates: list) -> list[str]:
    return await run_in_executor(_batch_update_sheet, updates)

async def execute_fill(updates: list, month_num: str) -> tuple[int, list[str]]:
    return await run_in_executor(_execute_fill_sync, updates, month_num)

async def get_current_snapshot() -> dict:
    return await run_in_executor(_get_current_snapshot_sync)

async def get_schedule_for_period(date_from: str, date_to: str) -> tuple:
    return await run_in_executor(_get_schedule_for_period_sync, date_from, date_to)

async def check_worksheet_exists(month_num: str) -> bool:
    return await run_in_executor(_check_worksheet_exists, month_num)

async def create_month_sheet(month_num: str, year: int):
    return await run_in_executor(_create_month_sheet, month_num, year)

async def get_workers_for_date(date_str: str) -> tuple[list, list, str | None]:
    return await run_in_executor(_get_workers_for_date_sync, date_str)
