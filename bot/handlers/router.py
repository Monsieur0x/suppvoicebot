import time
from datetime import datetime

from telegram import Update

from bot.config import MONTHS_SHEETS, PENDING_TTL, MSK, logger
from bot.state import pending_updates, last_batch
from bot.core.sheets import update_sheet, batch_update_sheet, run_in_executor
from bot.services.ai_client import parse_with_claude, generate_cheer_and_chat
from bot.handlers.actions import (
    safe_delete,
    handle_fill_schedule, handle_show_period, handle_show_workers,
    handle_show_history, handle_show_changes_period, handle_check_changes,
)


async def process_text(text: str, update: Update, user_id: int):
    logger.info(f"[PROCESS] user={user_id} text={text!r}")
    try:
        data = await run_in_executor(parse_with_claude, text, user_id)
    except RuntimeError as e:
        logger.warning(f"[PROCESS] RuntimeError от AI: {e}")
        await update.message.reply_text(str(e))
        return
    except Exception as e:
        logger.error(f"[PROCESS] неожиданная ошибка AI: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка соединения с ИИ. Попробуй ещё раз.")
        return

    action = data.get("action")
    logger.info(f"[PROCESS] action={action} data={data}")

    if action == "fill_schedule":
        month_num = str(data.get("month", datetime.now(MSK).strftime("%m"))).zfill(2)
        year = int(data.get("year", datetime.now(MSK).year))
        if month_num not in MONTHS_SHEETS:
            await update.message.reply_text(f"❌ Неизвестный месяц: {month_num}")
            return
        await handle_fill_schedule(month_num, year, user_id, update)

    elif action == "show_period":
        date_from = data.get("date_from")
        date_to = data.get("date_to")
        if not date_from or not date_to:
            await update.message.reply_text("⚠️ Не понял период. Уточни даты.")
            return
        await handle_show_period(date_from, date_to, update)

    elif action == "show_workers":
        date_str = data.get("date")
        if not date_str:
            await update.message.reply_text("⚠️ Не понял дату.")
            return
        await handle_show_workers(date_str, update)

    elif action == "show_history":
        await handle_show_history(update)

    elif action == "show_changes_period":
        date_from = data.get("date_from")
        date_to = data.get("date_to")
        if not date_from or not date_to:
            await update.message.reply_text("⚠️ Не понял период.")
            return
        await handle_show_changes_period(date_from, date_to, update)

    elif action == "check_changes":
        await handle_check_changes(update)

    elif action == "update":
        name = data.get("name")
        date_str = data.get("date")
        time_val = data.get("time")
        if name is None or date_str is None or time_val is None:
            await update.message.reply_text(f"⚠️ Не понял: Имя={name}, Дата={date_str}, Время={time_val}")
            return
        result = await update_sheet(name, date_str, time_val)
        await update.message.reply_text(result, parse_mode="Markdown")

    elif action == "update_many":
        updates = data.get("updates", [])
        if not updates:
            await update.message.reply_text("⚠️ Не понял кому и что менять.")
            return
        if len(updates) > 5:
            pending_updates[user_id] = {"updates": updates, "expires_at": time.time() + PENDING_TTL}
            names_list = list({u.get("name", "?") for u in updates})
            await update.message.reply_text(
                f"⚠️ Собираюсь изменить *{len(updates)} записей*\n"
                f"Кому: {', '.join(names_list)}\n\n"
                f"Подтвердить? Напиши *да* или *нет* (2 минуты)",
                parse_mode="Markdown",
            )
            return
        tmp_msg = await update.message.reply_text(f"⏳ Обновляю {len(updates)} записей...")
        results = await batch_update_sheet(updates)
        last_batch[user_id] = updates
        await safe_delete(tmp_msg)
        await update.message.reply_text("\n".join(results), parse_mode="Markdown")

    elif action == "undo":
        name = data.get("name")
        date_str = data.get("date")
        if name is None or date_str is None:
            await update.message.reply_text("⚠️ Не понял для кого и на какую дату.")
            return
        result = await update_sheet(name, date_str, "", is_undo=True)
        await update.message.reply_text(result, parse_mode="Markdown")

    elif action == "undo_batch":
        if user_id not in last_batch or not last_batch[user_id]:
            await update.message.reply_text("❌ Нет последнего массового обновления.")
            return
        updates = last_batch[user_id]
        tmp_msg = await update.message.reply_text(f"↩️ Откатываю {len(updates)} записей...")
        results = []
        for u in updates:
            r = await update_sheet(u["name"], u["date"], "", is_undo=True)
            results.append(r)
        last_batch.pop(user_id, None)
        await safe_delete(tmp_msg)
        await update.message.reply_text("\n".join(results), parse_mode="Markdown")

    elif action == "cheer":
        try:
            response = await run_in_executor(generate_cheer_and_chat, data.get("type", "support"), None)
        except RuntimeError as e:
            await update.message.reply_text(str(e))
            return
        await update.message.reply_text(response)

    elif action in ("chat", "unknown"):
        tmp_msg = await update.message.reply_text("💭 Думаю...")
        try:
            response = await run_in_executor(generate_cheer_and_chat, None, text)
        except RuntimeError as e:
            await safe_delete(tmp_msg)
            await update.message.reply_text(str(e))
            return
        except Exception:
            await safe_delete(tmp_msg)
            await update.message.reply_text("❌ Ошибка ИИ. Попробуй ещё раз.")
            return
        await safe_delete(tmp_msg)
        await update.message.reply_text(response)

    else:
        await update.message.reply_text("🤔 Не понял запрос. Попробуй переформулировать.")
