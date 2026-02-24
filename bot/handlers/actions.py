import os
import time
from datetime import datetime
from calendar import monthrange

from telegram import Update

from bot.config import MONTHS_RU, MONTHS_SHEETS, MSK, NAMES, PENDING_TTL, logger
from bot.state import (
    history, snapshot, sheets_cache, sheets_cache_time,
    pending_fill, last_batch, save_snapshot,
)
from bot.core.sheets import (
    update_sheet, batch_update_sheet, execute_fill,
    get_current_snapshot, get_schedule_for_period,
    check_worksheet_exists, create_month_sheet,
    get_workers_for_date, run_in_executor,
)
from bot.core.schedule import generate_month_updates
from bot.core.image_gen import generate_schedule_image


async def safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass


def compare_snapshots(old: dict, new: dict) -> list:
    changes = []
    for key in set(old.keys()) | set(new.keys()):
        old_val = old.get(key, "—")
        new_val = new.get(key, "—")
        if old_val != new_val and old_val != "—" and new_val != "—":
            parts = key.split("_", 1)
            if len(parts) == 2:
                changes.append({"name": parts[0], "date": parts[1], "old": old_val, "new": new_val})
    return changes


async def handle_fill_schedule(month_num: str, year: int, user_id: int, update: Update):
    month_name = MONTHS_RU.get(month_num, month_num)
    days_in_month = monthrange(year, int(month_num))[1]
    total = days_in_month * len(NAMES)

    tmp_msg = await update.message.reply_text(f"🔍 Проверяю лист {month_name} {year}...")

    sheet_created = False
    exists = await check_worksheet_exists(month_num)
    if not exists:
        try:
            await create_month_sheet(month_num, year)
            sheet_created = True
        except Exception as create_err:
            await safe_delete(tmp_msg)
            await update.message.reply_text(f"❌ Не удалось создать лист: {create_err}")
            return

    await safe_delete(tmp_msg)

    updates = generate_month_updates(month_num, year)
    created_text = " _(лист создан автоматически)_" if sheet_created else ""

    pending_fill[user_id] = {
        "month": month_num,
        "year": year,
        "updates": updates,
        "expires_at": time.time() + PENDING_TTL,
    }
    await update.message.reply_text(
        f"📅 Заполню *{month_name} {year}* по паттерну 2/2{created_text}\n"
        f"Сотрудников: {len(NAMES)}, дней: {days_in_month}, записей: {total}\n\n"
        f"Подтвердить? Напиши *да* или *нет* (2 минуты).",
        parse_mode="Markdown",
    )


async def execute_fill_action(pending: dict, user_id: int, update: Update):
    month_name = MONTHS_RU.get(pending["month"], pending["month"])
    year = pending["year"]
    tmp_msg = await update.message.reply_text(
        f"⏳ Заполняю *{month_name} {year}* по паттерну 2/2...",
        parse_mode="Markdown",
    )

    total_ok, total_err = await execute_fill(pending["updates"], pending["month"])
    last_batch[user_id] = pending["updates"]
    await safe_delete(tmp_msg)

    if total_err:
        err_text = "\n".join(total_err[:5])
        if len(total_err) > 5:
            err_text += f"\n...и ещё {len(total_err) - 5} ошибок"
        await update.message.reply_text(
            f"✅ Заполнено {total_ok}\n❌ Ошибок: {len(total_err)}\n\n{err_text}",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"✅ *{month_name} {year}* заполнен по паттерну 2/2!\nЗаписей: {total_ok}",
            parse_mode="Markdown",
        )


async def handle_show_period(date_from: str, date_to: str, update: Update):
    tmp_msg = await update.message.reply_text("📊 Генерирую расписание...")
    result = await get_schedule_for_period(date_from, date_to)
    await safe_delete(tmp_msg)

    title, headers, rows_or_error = result
    if title is None:
        await update.message.reply_text(rows_or_error)
        return

    img_path = await run_in_executor(generate_schedule_image, title, headers, rows_or_error)
    try:
        with open(img_path, "rb") as f:
            await update.message.reply_photo(f)
    finally:
        if os.path.exists(img_path):
            os.unlink(img_path)


async def handle_show_workers(date_str: str, update: Update):
    tmp_msg = await update.message.reply_text("🔍 Ищу кто работает...")
    try:
        workers, off, error = await get_workers_for_date(date_str)
        await safe_delete(tmp_msg)
        if error:
            await update.message.reply_text(error)
            return

        parts = date_str.split(".")
        day_z = parts[0].zfill(2)
        month_z = parts[1].zfill(2)
        year = datetime.now(MSK).year

        lines = [f"👥 *Кто работает {day_z}.{month_z}.{year}:*\n"]
        lines.extend(workers if workers else ["Никто не работает"])
        if off:
            lines.append(f"\n😴 *Выходной:* {', '.join(off)}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await safe_delete(tmp_msg)
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def handle_show_history(update: Update):
    if not history:
        await update.message.reply_text("📋 История изменений пуста.")
        return
    lines = ["📋 *Последние изменения:*\n"]
    for key, entry in list(history.items())[-15:]:
        name, date_key = key.split("_", 1)
        if isinstance(entry, dict):
            lines.append(
                f"• *{name}* / {date_key}\n  {entry['old']} → {entry['new']} _({entry.get('changed_at', '—')})_"
            )
        else:
            lines.append(f"• {name} / {date_key} _(было: {entry})_")
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def handle_show_changes_period(date_from: str, date_to: str, update: Update):
    year = datetime.now(MSK).year
    try:
        d1 = datetime.strptime(f"{date_from}.{year}", "%d.%m.%Y")
        d2 = datetime.strptime(f"{date_to}.{year}", "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат дат")
        return

    lines = [f"📋 *Изменения за {date_from} — {date_to}:*\n"]
    found = False
    for key, entry in history.items():
        name, date_key = key.split("_", 1)
        try:
            p = date_key.split(".")
            entry_date = datetime.strptime(f"{p[0]}.{p[1]}.{year}", "%d.%m.%Y")
            if d1 <= entry_date <= d2:
                found = True
                if isinstance(entry, dict):
                    lines.append(
                        f"• *{name}* / {date_key}\n  {entry['old']} → {entry['new']} _({entry.get('changed_at', '—')})_"
                    )
                else:
                    lines.append(f"• {name} / {date_key} _(было: {entry})_")
        except Exception:
            continue
    if not found:
        await update.message.reply_text(f"📋 Изменений за {date_from} — {date_to} не найдено.")
        return
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def handle_check_changes(update: Update):
    tmp_msg = await update.message.reply_text("🔄 Проверяю изменения в таблице...")
    new_snapshot = await get_current_snapshot()
    if not snapshot:
        snapshot.update(new_snapshot)
        save_snapshot(new_snapshot)
        await safe_delete(tmp_msg)
        await update.message.reply_text("✅ Снимок таблицы сохранён!")
        return
    changes = compare_snapshots(snapshot, new_snapshot)
    sheets_cache.clear()
    sheets_cache_time.clear()
    snapshot.clear()
    snapshot.update(new_snapshot)
    save_snapshot(new_snapshot)
    await safe_delete(tmp_msg)
    if not changes:
        await update.message.reply_text("✅ Изменений нет — таблица актуальна.")
        return
    lines = [f"📋 *Найдено изменений: {len(changes)}*\n"]
    for c in changes:
        lines.append(f"👤 *{c['name']}* / {c['date']}\n   {c['old']} → *{c['new']}*")
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
