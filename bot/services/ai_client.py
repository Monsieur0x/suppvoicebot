import json
import re
from datetime import datetime

import anthropic

from bot.config import ANTHROPIC_API_KEY, NAMES, DAYS_RU, MONTHS_SHEETS, MSK, logger
from bot.state import append_user_context, get_user_context

_client = None

PARSE_MODEL = "claude-haiku-4-5"
CHAT_MODEL = "claude-haiku-4-5"

VALID_ACTIONS = {
    "update", "update_many", "show_period", "show_history",
    "show_changes_period", "show_workers", "check_changes",
    "fill_schedule", "undo", "undo_batch",
    "cheer", "chat", "unknown",
}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


# ===================== КЛАССИФИКАЦИЯ ОШИБОК =====================


def _classify_error(e: Exception) -> RuntimeError:
    """Превращает ошибку Anthropic API в понятное сообщение для пользователя."""
    if isinstance(e, anthropic.RateLimitError):
        logger.warning(f"Claude 429 rate limit: {e}")
        return RuntimeError("Слишком много запросов, подожди немного")
    if isinstance(e, anthropic.AuthenticationError):
        logger.error("Claude auth error (401)")
        return RuntimeError("Ошибка авторизации ИИ — проверь API-ключ")
    if isinstance(e, anthropic.PermissionDeniedError):
        logger.error("Claude permission denied (403)")
        return RuntimeError("Нет доступа к ИИ — проверь права ключа")
    if isinstance(e, anthropic.APIConnectionError):
        logger.warning(f"Claude connection error: {e}")
        return RuntimeError("Нет соединения с ИИ, попробуй позже")
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code >= 500:
            logger.warning(f"Claude server error {e.status_code}: {e}")
            return RuntimeError("Сервер ИИ временно недоступен, попробуй позже")
        logger.error(f"Claude API error {e.status_code}: {e}")
        return RuntimeError(f"Ошибка ИИ: {str(e)[:100]}")
    logger.error(f"Claude неизвестная ошибка: {e}", exc_info=True)
    return RuntimeError(f"Ошибка ИИ: {str(e)[:100]}")


# ===================== СИСТЕМНЫЙ ПРОМПТ =====================


def _get_system_prompt() -> str:
    today = datetime.now(MSK)
    year = today.year
    return f"""Ты помощник для управления расписанием сотрудников.
Сегодняшняя дата: {today.strftime('%d.%m.%Y')}, {DAYS_RU[today.weekday()]}.
Текущий год: {year}.
Сотрудники: {', '.join(NAMES)}.

Твоя задача — распознать намерение пользователя и вернуть JSON.

Возможные действия:
- "update" — изменить время одному сотруднику
- "update_many" — изменить время нескольким
- "show_period" — расписание за период/день
- "show_history" — история изменений через бота
- "show_changes_period" — кто менялся за период
- "show_workers" — кто работает в конкретный день
- "check_changes" — проверить изменения в таблице
- "fill_schedule" — заполнить месяц по паттерну 2/2
- "undo" — вернуть предыдущее значение
- "undo_batch" — отменить последнее массовое обновление
- "cheer" — похвалить/поддержать/подбодрить
- "chat" — свободный разговор
- "unknown" — непонятный запрос

Форматы:
update: {{"action":"update","name":"Вова","date":"18.02","time":"13:00 - 21:00"}}
update_many: {{"action":"update_many","updates":[{{"name":"Вова","date":"18.02","time":"13:00 - 21:00"}}]}}
show_period: {{"action":"show_period","date_from":"18.02","date_to":"18.02"}}
show_history: {{"action":"show_history"}}
show_changes_period: {{"action":"show_changes_period","date_from":"11.02","date_to":"18.02"}}
show_workers: {{"action":"show_workers","date":"18.02"}}
check_changes: {{"action":"check_changes"}}
fill_schedule: {{"action":"fill_schedule","month":"03","year":{year}}}
undo: {{"action":"undo","name":"Вова","date":"18.02"}}
undo_batch: {{"action":"undo_batch"}}
cheer: {{"action":"cheer","type":"praise"}}
chat: {{"action":"chat"}}
unknown: {{"action":"unknown"}}

Правила fill_schedule (ТОЛЬКО если есть слово "заполни" или "создай расписание"):
- "заполни март" → month="03", year={year}
- "заполни следующий месяц" → следующий месяц
- "заполни май 2027" → month="05", year=2027
- Месяц всегда 2 цифры: "03", "04" и т.д.
- Если год не указан → {year}

ВАЖНО — разница между действиями:
- "заполни май", "создай расписание на май" → fill_schedule (ЗАПИСАТЬ в таблицу)
- "расписание на май", "покажи май", "смены на май", "график на май" → show_period (ПОКАЗАТЬ картинку)
- fill_schedule используется ТОЛЬКО когда пользователь хочет ЗАПИСАТЬ/СОЗДАТЬ/ЗАПОЛНИТЬ данные в таблице
- show_period используется когда пользователь хочет ПОСМОТРЕТЬ/УВИДЕТЬ расписание

Общие правила:
- Время: "HH:MM - HH:MM" или "Выходной"
- Дата: "DD.MM"
- Если запрос на целый месяц → date_from="01.MM", date_to="последний день.MM"
- "сегодня/завтра/послезавтра" — от сегодня
- "эта неделя" — с сегодня до воскресенья
- "следующая неделя" — пн-вс след. недели
- Имена в любом падеже: Вове → "Вова"
- "кто менялся" → show_history/show_changes_period, НЕ show_period
- "кто работает" → show_workers, НЕ show_period
- "расписание/смены/график/покажи" → show_period
- Верни ТОЛЬКО валидный JSON без markdown"""


# ===================== ВАЛИДАЦИЯ ОТВЕТА LLM =====================


def _validate_parsed(result: dict) -> dict | None:
    action = result.get("action")
    if action not in VALID_ACTIONS:
        logger.warning(f"LLM вернул невалидный action: {action}")
        return None

    if action == "update_many":
        updates = result.get("updates")
        if not isinstance(updates, list):
            logger.warning("LLM: update_many без списка updates")
            return None

    if action == "fill_schedule":
        month = str(result.get("month", "")).zfill(2)
        if month not in MONTHS_SHEETS:
            logger.warning(f"LLM: невалидный месяц для fill_schedule: {month}")
            return None
        result["month"] = month

    return result


# ===================== ПАРСИНГ =====================


def parse_with_claude(text: str, user_id: int) -> dict:
    """Классифицирует намерение пользователя через Claude API."""
    logger.info(f"[PARSE] user={user_id} text={text!r}")
    try:
        today = datetime.now(MSK)
        messages = []

        ctx = get_user_context(user_id)[-3:]
        if ctx:
            logger.debug(f"[PARSE] контекст пользователя ({len(ctx)} сообщ.): {ctx}")
        for prev in ctx:
            messages.append({"role": "user", "content": prev})
            messages.append({"role": "assistant", "content": '{"action":"chat"}'})

        messages.append({
            "role": "user",
            "content": f"Сегодня {today.strftime('%d.%m.%Y')} ({DAYS_RU[today.weekday()]}). Запрос: {text}",
        })

        logger.debug(f"[PARSE] отправляю {len(messages)} сообщений в Claude ({PARSE_MODEL})")
        response = _get_client().messages.create(
            model=PARSE_MODEL,
            max_tokens=1000,
            system=_get_system_prompt(),
            messages=messages,
        )

        raw = response.content[0].text.strip()
        logger.debug(f"[PARSE] raw ответ Claude: {raw!r}")
        logger.debug(f"[PARSE] usage: input={response.usage.input_tokens} output={response.usage.output_tokens}")

        raw = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(raw)
        logger.info(f"[PARSE] результат: {result}")

        validated = _validate_parsed(result)
        if validated is None:
            logger.warning("[PARSE] ответ LLM не прошёл валидацию, fallback на chat")
            validated = {"action": "chat"}

        append_user_context(user_id, text)
        return validated

    except (json.JSONDecodeError, IndexError, KeyError) as e:
        logger.error(f"[PARSE] ошибка парсинга ответа Claude: {e}", exc_info=True)
        return {"action": "chat"}
    except Exception as e:
        logger.error(f"[PARSE] ошибка API: {e}")
        raise _classify_error(e) from e


# ===================== ГЕНЕРАЦИЯ ТЕКСТА =====================


def generate_cheer_and_chat(cheer_type: str = None, chat_text: str = None) -> str:
    """Генерирует ответ для подбадривания или свободного чата."""
    if cheer_type:
        prompts = {
            "praise":   "Похвали пользователя — скажи что он молодец, 2-3 предложения",
            "support":  "Поддержи пользователя — ему тяжело, 2-3 предложения",
            "pity":     "Пожалей по-доброму, с лёгким юмором, 2-3 предложения",
            "motivate": "Подбодри энергично, 2-3 предложения",
        }
        user_text = prompts.get(cheer_type, prompts["support"])
        system = (
            "Ты дружелюбный помощник в Telegram-боте. Отвечай на русском.\n\n"
            "Правила оформления:\n"
            "- Пиши живо и естественно, как друг в чате\n"
            "- Эмодзи ставь в начале фразы или между предложениями, НЕ перед точкой\n"
            "- Не лепи эмодзи к каждому слову — максимум 2-3 на сообщение\n"
            "- Разбивай текст на короткие предложения, каждое с новой строки\n"
            "- Не заканчивай предложение эмодзи+точка (плохо: «Молодец 🎉.»)"
        )
        max_tokens = 150
    else:
        user_text = chat_text
        system = (
            "Ты ИИ-помощник в Telegram-боте для управления расписанием сотрудников.\n"
            "Можешь говорить на любые темы — работа, жизнь, наука, технологии.\n"
            "Если спрашивают кто ты — говори что ИИ-помощник этого бота.\n\n"
            "Правила оформления ответов:\n"
            "- Отвечай на русском, живо и по делу\n"
            "- Структурируй ответ: разбивай на абзацы, используй переносы строк\n"
            "- Эмодзи можно использовать умеренно (1-3 на сообщение), но:\n"
            "  * Ставь эмодзи в начале фразы или отдельно, НЕ перед точкой\n"
            "  * Плохо: «Это интересно 🤔.» Хорошо: «🤔 Это интересно.» или «Это интересно!»\n"
            "  * Не нужно эмодзи в каждом предложении\n"
            "- Если ответ длинный — разбей на короткие абзацы\n"
            "- Не пиши стену текста одним предложением"
        )
        max_tokens = 500

    try:
        mode = f"cheer:{cheer_type}" if cheer_type else "chat"
        logger.info(f"[CHAT] mode={mode} text={user_text!r:.80}")
        response = _get_client().messages.create(
            model=CHAT_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        result = response.content[0].text.strip()
        logger.info(f"[CHAT] ответ ({len(result)} симв.): {result!r:.100}")
        logger.debug(f"[CHAT] usage: input={response.usage.input_tokens} output={response.usage.output_tokens}")
        return result
    except Exception as e:
        logger.error(f"[CHAT] ошибка API: {e}")
        raise _classify_error(e) from e
