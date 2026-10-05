import threading

from google import genai
from google.genai import types

from app.config import get_settings

_client_lock = threading.RLock()
_client_instance: genai.Client | None = None


def get_genai_client() -> genai.Client:
    """Return process-singleton genai.Client created lazily with a 60-second timeout."""
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                settings = get_settings()
                _client_instance = genai.Client(
                    api_key=settings.gemini_api_key,
                    http_options=types.HttpOptions(timeout=60_000),
                )
    return _client_instance
