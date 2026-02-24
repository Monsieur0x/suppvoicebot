import sys
from datetime import time as dt_time

from telegram.ext import ApplicationBuilder, MessageHandler, filters

from bot.config import (
    TELEGRAM_TOKEN, SCHEDULE_CHAT_ID, SCHEDULE_THREAD_ID, SCHEDULE_TIME, logger,
)
from bot.handlers import handle_voice, handle_text, error_handler, send_daily_schedule
from bot.core.sheets import shutdown_executor


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN не задан в .env")
        sys.exit(1)

    logger.info("Бот запускается...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(error_handler)

    # Ежедневная отправка расписания
    if SCHEDULE_CHAT_ID and SCHEDULE_THREAD_ID:
        try:
            h, m = SCHEDULE_TIME.split(":")
            send_time = dt_time(hour=int(h), minute=int(m), second=0)
            app.job_queue.run_daily(send_daily_schedule, time=send_time)
            logger.info(
                f"Автоотправка расписания: {SCHEDULE_TIME} → "
                f"chat={SCHEDULE_CHAT_ID}, thread={SCHEDULE_THREAD_ID}"
            )
        except Exception as e:
            logger.error(f"Не удалось настроить автоотправку: {e}")
    else:
        logger.info("Автоотправка расписания не настроена (нет SCHEDULE_CHAT_ID/SCHEDULE_THREAD_ID)")

    logger.info("Бот запущен!")
    try:
        app.run_polling(stop_signals=None)
    except KeyboardInterrupt:
        logger.info("Получен Ctrl+C, останавливаюсь...")
    finally:
        shutdown_executor()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    main()
