import os
import tempfile
import time

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import RATE_LIMIT_SECONDS, logger
from bot.state import user_last_request
from bot.services.voice import transcribe_voice
from bot.core.sheets import run_in_executor
from bot.handlers.actions import safe_delete
from bot.handlers.confirmations import handle_confirmation
from bot.handlers.router import process_text


def _is_telegram_network_error(e: Exception) -> bool:
    err = str(e).lower()
    return "timed out" in err or "timeout" in err or "connect" in err


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return
    try:
        await _handle_voice_inner(update, context)
    except Exception as e:
        if _is_telegram_network_error(e):
            logger.warning(f"Telegram недоступен (voice): {e}")
        else:
            logger.error(f"Ошибка handle_voice: {e}", exc_info=True)


async def _handle_voice_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    duration = update.message.voice.duration
    logger.info(f"[VOICE] user={user_id} duration={duration}s")

    if duration < 1:
        logger.debug(f"[VOICE] user={user_id} слишком короткое ({duration}s)")
        await update.message.reply_text("⚠️ Слишком короткое сообщение.")
        return
    now = time.time()
    if now - user_last_request[user_id] < RATE_LIMIT_SECONDS:
        logger.debug(f"[VOICE] user={user_id} rate limit")
        await update.message.reply_text("⏳ Подожди немного...")
        return
    user_last_request[user_id] = now

    tmp_msg = await update.message.reply_text("🎙 Обрабатываю...")
    file = await context.bot.get_file(update.message.voice.file_id)

    fd, path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        logger.debug(f"[VOICE] скачиваю файл → {path}")
        await file.download_to_drive(path)
        logger.debug(f"[VOICE] транскрибирую...")
        text = await run_in_executor(transcribe_voice, path)
        logger.info(f"[VOICE] user={user_id} распознано: {text!r}")
    except Exception as e:
        logger.error(f"[VOICE] ошибка транскрибации: {e}", exc_info=True)
        await safe_delete(tmp_msg)
        await update.message.reply_text("❌ Не смог распознать. Попробуй ещё раз.")
        return
    finally:
        if os.path.exists(path):
            os.unlink(path)

    await safe_delete(tmp_msg)
    recognized_msg = await update.message.reply_text(f"📝 Распознал: {text}")
    await process_text(text, update, user_id)
    await safe_delete(recognized_msg)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    try:
        await _handle_text_inner(update, context)
    except Exception as e:
        if _is_telegram_network_error(e):
            logger.warning(f"Telegram недоступен (text): {e}")
        else:
            logger.error(f"Ошибка handle_text: {e}", exc_info=True)


async def _handle_text_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    user_id = update.message.from_user.id
    chat_type = update.message.chat.type
    logger.info(f"[TEXT] user={user_id} chat={chat_type} text={message_text!r:.100}")

    if chat_type in ["group", "supergroup"]:
        if f"@{context.bot.username}" not in message_text:
            return
        message_text = message_text.replace(f"@{context.bot.username}", "").strip()
        logger.debug(f"[TEXT] после удаления @mention: {message_text!r}")
    if await handle_confirmation(update, user_id, message_text):
        logger.debug(f"[TEXT] user={user_id} обработано как подтверждение")
        return
    await process_text(message_text, update, user_id)


async def error_handler(update, context):
    err = context.error
    err_str = str(err).lower()
    if "timed out" in err_str or "timeout" in err_str or "connecttimeout" in err_str:
        logger.warning(f"Telegram недоступен: {err}")
        return
    logger.error(f"Ошибка: {err}", exc_info=True)
    if update and update.message:
        try:
            await update.message.reply_text("❌ Внутренняя ошибка. Попробуй ещё раз.")
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение об ошибке: {e}")
