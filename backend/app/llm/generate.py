import hashlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from google.genai import errors, types
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
)

from app.config import get_settings
from app.db import get_db
from app.db.paths import llm_cache_path
from app.llm.client import get_genai_client
from app.llm.exceptions import ModelUnavailable, QuotaExhausted, UnreadableOutput


def parse_retry_delay(exc: errors.ClientError) -> int:
    """Extract retryDelay from ClientError details, rounding up fractional seconds."""
    details = exc.details
    if isinstance(details, dict):
        detail_list = details.get("error", {}).get("details", []) or details.get("details", [])
        if isinstance(detail_list, list):
            for item in detail_list:
                if isinstance(item, dict):
                    type_str = str(item.get("@type", ""))
                    if type_str.endswith("RetryInfo") or "RetryInfo" in type_str:
                        delay_val = item.get("retryDelay")
                        if delay_val is not None:
                            match = re.search(r"(\d+(?:\.\d+)?)\s*s?", str(delay_val))
                            if match:
                                return max(1, math.ceil(float(match.group(1))))
    return 60


# Worst case runtime: 3 attempts * (60s timeout + backoff) bounded by stop_after_delay(100s).
# Two generation attempts give worst case 2 * 100s = 200s, well within Cloud Run 300s.
@retry(
    reraise=True,
    stop=stop_after_attempt(3) | stop_after_delay(100),
    wait=wait_random_exponential(multiplier=1, max=10),
    retry=retry_if_exception_type((
        errors.ServerError,
        httpx.TimeoutException,
        httpx.NetworkError,
        TimeoutError,
        ConnectionError,
    )),
)
def _call_gemini_api(
    model: str,
    contents: Any,
    config: types.GenerateContentConfig,
) -> types.GenerateContentResponse:
    client = get_genai_client()
    try:
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
    except errors.ClientError as exc:
        if exc.code == 429:
            retry_s = parse_retry_delay(exc)
            raise QuotaExhausted(retry_after_s=retry_s) from exc
        raise


def generate_json[T: BaseModel](
    prompt_version: str,
    system_instruction: str,
    contents: str,
    schema: type[T],
    cache_key_parts: list[str],
) -> T:
    """Return validated schema instance, reading cache first and retrying unreadable output once."""
    settings = get_settings()
    model = settings.gemini_model

    # Cache key includes sha256 of full prompt (system instruction + contents)
    prompt_hash = hashlib.sha256(f"{system_instruction}\n\n{contents}".encode()).hexdigest()
    parts = [model, prompt_version, prompt_hash, *(str(p) for p in cache_key_parts)]
    cache_key = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    db = get_db()
    cache_ref = db.document(llm_cache_path(cache_key))
    cached_doc = cache_ref.get()
    if cached_doc.exists:
        data = cached_doc.to_dict() or {}
        return schema.model_validate(data.get("output"))

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_level="low"),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        max_output_tokens=2000,
    )

    # Attempt generation with 1 schema-validation retry
    for _ in range(2):
        try:
            response = _call_gemini_api(model=model, contents=contents, config=config)
        except (
            errors.ServerError,
            httpx.TimeoutException,
            httpx.NetworkError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            raise ModelUnavailable(
                "The AI model is busy right now. Please try again in a minute."
            ) from exc
        raw_text = getattr(response, "text", None)
        if raw_text is not None and raw_text.strip():
            try:
                validated = schema.model_validate_json(raw_text)
                cache_ref.set({
                    "model": model,
                    "prompt_version": prompt_version,
                    "output": validated.model_dump(mode="json"),
                    "created_at": datetime.now(UTC),
                })
                return validated
            except (ValidationError, json.JSONDecodeError, ValueError):
                pass

    raise UnreadableOutput("The AI model returned an unreadable answer. Please try again.")
