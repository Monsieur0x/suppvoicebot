import os
import json
import logging
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))
from dotenv import load_dotenv

# ===================== ENV =====================

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SERVICE_ACCOUNT_PATH = os.getenv("SERVICE_ACCOUNT_PATH", "service_account.json")

HISTORY_FILE = "history.json"
SNAPSHOT_FILE = "snapshot.json"
EMPLOYEES_FILE = "employees.json"

CACHE_TTL = 60
RATE_LIMIT_SECONDS = 3
GC_CHECK_INTERVAL = 300
PENDING_TTL = 120

# Автоотправка расписания в топик
SCHEDULE_CHAT_ID = int(os.getenv("SCHEDULE_CHAT_ID", "0"))
SCHEDULE_THREAD_ID = int(os.getenv("SCHEDULE_THREAD_ID", "0"))
SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "08:00")  # HH:MM по локальному времени

# ===================== ЛОГИРОВАНИЕ =====================

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
# Внешние библиотеки — только WARNING, чтобы не спамить
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("gspread").setLevel(logging.INFO)
logging.getLogger("google").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger("bot")

# ===================== КОНСТАНТЫ =====================

MONTHS_SHEETS = {
    "01": "Январь_1",   "02": "Февраль_1",  "03": "Март_1",
    "04": "Апрель_1",   "05": "Май_1",       "06": "Июнь_1",
    "07": "Июль_1",     "08": "Август_1",    "09": "Сентябрь_1",
    "10": "Октябрь_1",  "11": "Ноябрь_1",    "12": "Декабрь_1",
}

MONTHS_RU = {
    "01": "Январь",   "02": "Февраль",  "03": "Март",
    "04": "Апрель",   "05": "Май",       "06": "Июнь",
    "07": "Июль",     "08": "Август",    "09": "Сентябрь",
    "10": "Октябрь",  "11": "Ноябрь",    "12": "Декабрь",
}

DAYS_RU = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}

# ===================== СОТРУДНИКИ =====================


def load_employees() -> dict:
    if os.path.exists(EMPLOYEES_FILE):
        try:
            with open(EMPLOYEES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки employees.json: {e}")
    return {}


EMPLOYEES_CONFIG = load_employees()
ANCHOR_DATE = datetime.strptime(
    EMPLOYEES_CONFIG.get("anchor_date", "2026-02-01"), "%Y-%m-%d"
).date()
EMPLOYEES = EMPLOYEES_CONFIG.get("employees", {})
NAMES = list(EMPLOYEES.keys())
