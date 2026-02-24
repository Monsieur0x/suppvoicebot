import json
import threading
from collections import OrderedDict, defaultdict
from datetime import datetime

from bot.config import HISTORY_FILE, SNAPSHOT_FILE, MSK, logger

# ===================== LOCK =====================

_state_lock = threading.Lock()

# ===================== ПЕРСИСТЕНТНЫЕ ДАННЫЕ =====================


def load_history() -> OrderedDict:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return OrderedDict(json.load(f))
    except FileNotFoundError:
        return OrderedDict()
    except Exception as e:
        logger.error(f"Ошибка загрузки history.json: {e}")
        return OrderedDict()


def _save_history_to_disk():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(history), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения истории: {e}")


def load_snapshot() -> dict:
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"Ошибка загрузки snapshot.json: {e}")
        return {}


def save_snapshot(data: dict):
    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения снимка: {e}")


# ===================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ =====================

history: OrderedDict = load_history()
snapshot: dict = load_snapshot()

sheets_cache: dict = {}
sheets_cache_time: dict[str, float] = {}

user_last_request: dict[int, float] = defaultdict(float)
user_context: OrderedDict = OrderedDict()

pending_updates: dict = {}
pending_fill: dict = {}
last_batch: dict = {}

MAX_HISTORY = 500
MAX_USER_CONTEXT = 500
MAX_CONTEXT_MESSAGES = 10


# ===================== ОПЕРАЦИИ НАД СОСТОЯНИЕМ =====================


def save_history_entry(key: str, old_val: str, new_val: str):
    with _state_lock:
        if len(history) > MAX_HISTORY:
            history.popitem(last=False)
        history[key] = {
            "old": old_val,
            "new": new_val,
            "changed_at": datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S"),
        }
    _save_history_to_disk()


def delete_history_entry(key: str):
    with _state_lock:
        history.pop(key, None)
    _save_history_to_disk()


def invalidate_cache(month: str):
    with _state_lock:
        sheets_cache.pop(month, None)
        sheets_cache_time.pop(month, None)


def append_user_context(user_id: int, text: str):
    with _state_lock:
        if user_id not in user_context:
            user_context[user_id] = []
        user_context[user_id].append(text)
        if len(user_context[user_id]) > MAX_CONTEXT_MESSAGES:
            user_context[user_id].pop(0)
        user_context.move_to_end(user_id)
        while len(user_context) > MAX_USER_CONTEXT:
            user_context.popitem(last=False)


def get_user_context(user_id: int) -> list[str]:
    return list(user_context.get(user_id, []))
