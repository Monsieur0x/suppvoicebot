import os
from datetime import datetime

from bot.config import SCHEDULE_CHAT_ID, SCHEDULE_THREAD_ID, MSK, logger
from bot.core.sheets import get_schedule_for_period, run_in_executor
from bot.core.image_gen import generate_schedule_image


async def send_daily_schedule(context):
    """Отправляет расписание на сегодня в топик беседы. Вызывается по расписанию."""
    if not SCHEDULE_CHAT_ID or not SCHEDULE_THREAD_ID:
        logger.warning("SCHEDULE_CHAT_ID или SCHEDULE_THREAD_ID не заданы, пропускаю")
        return
    try:
        today = datetime.now(MSK)
        date_str = today.strftime("%d.%m")

        title, headers, rows_or_error = await get_schedule_for_period(date_str, date_str)
        if title is None:
            await context.bot.send_message(
                chat_id=SCHEDULE_CHAT_ID,
                message_thread_id=SCHEDULE_THREAD_ID,
                text=f"📅 Расписание на {date_str} — данных нет",
            )
            return

        img_path = await run_in_executor(generate_schedule_image, title, headers, rows_or_error)
        try:
            with open(img_path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=SCHEDULE_CHAT_ID,
                    message_thread_id=SCHEDULE_THREAD_ID,
                    photo=f,
                    caption=f"📅 Расписание на {date_str} ({today.strftime('%A')})",
                )
            logger.info(f"Расписание на {date_str} отправлено в топик {SCHEDULE_THREAD_ID}")
        finally:
            if os.path.exists(img_path):
                os.unlink(img_path)
    except Exception as e:
        logger.error(f"Ошибка отправки расписания по расписанию: {e}", exc_info=True)
