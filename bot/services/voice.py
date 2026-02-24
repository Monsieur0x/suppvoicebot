import os

from groq import Groq

from bot.config import GROQ_API_KEY, logger

_groq_client = None


def _get_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


def transcribe_voice(ogg_path: str) -> str:
    """Транскрибирует голосовое сообщение через Groq Whisper API."""
    try:
        with open(ogg_path, "rb") as f:
            transcription = _get_client().audio.transcriptions.create(
                file=(os.path.basename(ogg_path), f.read()),
                model="whisper-large-v3",
                language="ru",
            )
        return transcription.text.strip()
    except Exception as e:
        logger.error(f"Ошибка транскрибации: {e}")
        raise
