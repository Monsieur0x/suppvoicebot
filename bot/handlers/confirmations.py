import time

from telegram import Update

from bot.state import pending_fill, pending_updates, last_batch
from bot.core.sheets import batch_update_sheet
from bot.handlers.actions import safe_delete, execute_fill_action

YES_WORDS = ("да", "да!", "подтверждаю", "ок", "ok", "yes")
NO_WORDS = ("нет", "отмена", "cancel", "no")


async def handle_confirmation(update: Update, user_id: int, text: str) -> bool:
    text_lower = text.lower().strip()

    if user_id in pending_fill:
        pending = pending_fill[user_id]
        if time.time() > pending["expires_at"]:
            pending_fill.pop(user_id)
            await update.message.reply_text("⏰ Время истекло. Повтори команду.")
            return True
        if text_lower in YES_WORDS:
            p = pending_fill.pop(user_id)
            await execute_fill_action(p, user_id, update)
            return True
        elif text_lower in NO_WORDS:
            pending_fill.pop(user_id)
            await update.message.reply_text("❌ Заполнение отменено.")
            return True

    if user_id in pending_updates:
        pending = pending_updates[user_id]
        if time.time() > pending["expires_at"]:
            pending_updates.pop(user_id)
            await update.message.reply_text("⏰ Время истекло. Повтори команду.")
            return True
        if text_lower in YES_WORDS:
            updates = pending_updates.pop(user_id)["updates"]
            tmp_msg = await update.message.reply_text(f"⏳ Обновляю {len(updates)} записей...")
            results = await batch_update_sheet(updates)
            last_batch[user_id] = updates
            await safe_delete(tmp_msg)
            await update.message.reply_text("\n".join(results), parse_mode="Markdown")
            return True
        elif text_lower in NO_WORDS:
            pending_updates.pop(user_id)
            await update.message.reply_text("❌ Отменено.")
            return True

    return False
