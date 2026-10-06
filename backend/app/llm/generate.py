import hashlib
import json
import logging
import math
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from google.genai import errors, types
from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    stop_before_delay,
    wait_random_exponential,
)

from app.config import get_settings
from app.db import get_db
from app.db.paths import llm_cache_path
from app.llm.client import get_genai_client
from app.llm.exceptions import ModelUnavailable, QuotaExhausted, UnreadableOutput

logger = logging.getLogger(__name__)

_clock = time.monotonic
_skip_map: dict[str, float] = {}
_skip_lock = threading.Lock()


def get_skip_until(model: str) -> float:
    with _skip_lock:
        return _skip_map.get(model, 0.0)


def set_skip_until(model: str, until: float) -> None:
    with _skip_lock:
        _skip_map[model] = until


def reset_skip_tracker() -> None:
    with _skip_lock:
        _skip_map.clear()


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


# Per model: up to 3 attempts; whole chain capped at 150 s by the caller's deadline.
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
    deadline: float | None = None,
) -> types.GenerateContentResponse:
    if deadline is not None:
        left = deadline - _clock()
        if left < 1.0:
            raise TimeoutError("Deadline exceeded before call attempt")
        config = config.model_copy(
            update={"http_options": types.HttpOptions(timeout=int(min(60.0, left) * 1000))}
        )
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


def generate_json_with_model[T: BaseModel](
    prompt_version: str,
    system_instruction: str,
    contents: Any,
    schema: type[T],
    cache_key_parts: list[str],
    models: list[str] | None = None,
) -> tuple[T, str]:
    """Return (validated_schema, model_name), checking cache first across chain."""
    settings = get_settings()
    chain = models if models is not None else settings.gemini_model_chain
    use_skips = len(chain) > 1

    db = get_db()
    prompt_hash = hashlib.sha256(f"{system_instruction}\n\n{contents}".encode()).hexdigest()

    # 1. Pre-call cache check across all models in chain in order
    for model in chain:
        parts = [model, prompt_version, prompt_hash, *(str(p) for p in cache_key_parts)]
        cache_key = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
        cache_ref = db.document(llm_cache_path(cache_key))
        cached_doc = cache_ref.get()
        if cached_doc.exists:
            data = cached_doc.to_dict() or {}
            hit_model = data.get("model", model)
            return schema.model_validate(data.get("output")), hit_model

    # 2. Chain execution with monotonic deadline
    start_time = _clock()
    deadline = start_time + 150.0
    failures: list[dict[str, Any]] = []

    for idx, model in enumerate(chain):
        left = deadline - _clock()
        if left < 1.0:
            failures.append({"type": "budget_exhausted"})
            break

        if use_skips:
            skip_until = get_skip_until(model)
            now = _clock()
            if skip_until > now:
                rem = max(1, math.ceil(skip_until - now))
                failures.append({"type": "429", "retry_after_s": rem})
                if idx + 1 < len(chain):
                    logger.info(
                        "Falling back from %s to %s (reason: skipped)",
                        model,
                        chain[idx + 1],
                    )
                continue

        parts = [model, prompt_version, prompt_hash, *(str(p) for p in cache_key_parts)]
        cache_key = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
        cache_ref = db.document(llm_cache_path(cache_key))

        # Schema validation retry (up to 2 generation attempts for this model)
        for _ in range(2):
            left = deadline - _clock()
            if left < 1.0:
                failures.append({"type": "budget_exhausted"})
                break

            attempt_timeout_ms = int(min(60.0, left) * 1000)
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                system_instruction=system_instruction,
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                max_output_tokens=2000,
                http_options=types.HttpOptions(timeout=attempt_timeout_ms),
            )

            call_fn = _call_gemini_api.retry_with(
                stop=stop_after_attempt(3) | stop_after_delay(100) | stop_before_delay(left - 1.0)
            )

            try:
                response = call_fn(
                    model=model,
                    contents=contents,
                    config=config,
                    deadline=deadline,
                )
            except QuotaExhausted as exc:
                if use_skips:
                    set_skip_until(model, _clock() + exc.retry_after_s)
                failures.append({"type": "429", "retry_after_s": exc.retry_after_s})
                if idx + 1 < len(chain):
                    logger.info(
                        "Falling back from %s to %s (reason: 429)",
                        model,
                        chain[idx + 1],
                    )
                break
            except (
                errors.ServerError,
                httpx.TimeoutException,
                httpx.NetworkError,
                TimeoutError,
                ConnectionError,
                RetryError,
            ):
                failures.append({"type": "unavailable"})
                if idx + 1 < len(chain):
                    logger.info(
                        "Falling back from %s to %s (reason: unavailable)",
                        model,
                        chain[idx + 1],
                    )
                break
            except errors.ClientError:
                # Other 4xx error (e.g. 400 Bad Request) stops chain immediately
                raise

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
                    return validated, model
                except (ValidationError, json.JSONDecodeError, ValueError):
                    pass
        else:
            raise UnreadableOutput("The AI model returned an unreadable answer. Please try again.")

        if failures and failures[-1].get("type") == "budget_exhausted":
            break

    # 3. All models in chain failed or budget exhausted
    if failures and all(f.get("type") == "429" for f in failures):
        min_wait = max(1, min(f["retry_after_s"] for f in failures))
        raise QuotaExhausted(retry_after_s=min_wait)

    raise ModelUnavailable("The AI model is busy right now. Please try again in a minute.")


def generate_json[T: BaseModel](
    prompt_version: str,
    system_instruction: str,
    contents: Any,
    schema: type[T],
    cache_key_parts: list[str],
    models: list[str] | None = None,
) -> T:
    """Return validated schema instance, reading cache first and retrying unreadable output once."""
    output, _ = generate_json_with_model(
        prompt_version=prompt_version,
        system_instruction=system_instruction,
        contents=contents,
        schema=schema,
        cache_key_parts=cache_key_parts,
        models=models,
    )
    return output
