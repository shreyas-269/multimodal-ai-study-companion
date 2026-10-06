import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from google.genai import errors
from pydantic import BaseModel
from tenacity import stop_before_delay, wait_none

from app.config import Settings, get_settings
from app.db import chunk_path, get_db, notebook_path
from app.db.paths import llm_cache_path
from app.llm import generate
from app.llm.exceptions import ModelUnavailable, QuotaExhausted, UnreadableOutput
from app.main import app
from tests.conftest import create_emulator_user

client = TestClient(app)


class DummyOutput(BaseModel):
    answer: str


def make_client_error_429(retry_delay: str = "45s") -> errors.ClientError:
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource exhausted",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": retry_delay,
                    }
                ],
            }
        },
    )


def test_chain_parsing():
    """Verify gemini_model_chain parses comma-separated list, stripping and de-duplicating."""
    s1 = Settings(
        gemini_api_key="key",
        jobs_runner_secret="secret",
        gemini_model="gemini-3.8-flash",
        gemini_fallback_models=" m-a , m-b ,, gemini-3.8-flash ",
        firebase_project_id="p",
        firebase_storage_bucket="b",
        cors_origins="*",
    )
    assert s1.gemini_model_chain == ["gemini-3.8-flash", "m-a", "m-b"]

    s2 = Settings(
        gemini_api_key="key",
        jobs_runner_secret="secret",
        gemini_model="gemini-3.8-flash",
        gemini_fallback_models=None,
        firebase_project_id="p",
        firebase_storage_bucket="b",
        cors_origins="*",
    )
    assert s2.gemini_model_chain == ["gemini-3.8-flash"]

    s3 = Settings(
        gemini_api_key="key",
        jobs_runner_secret="secret",
        gemini_model="gemini-3.8-flash",
        gemini_fallback_models="   ",
        firebase_project_id="p",
        firebase_storage_bucket="b",
        cors_origins="*",
    )
    assert s3.gemini_model_chain == ["gemini-3.8-flash"]


def test_single_model_chain_no_skips(monkeypatch, mock_gemini_client, cache_tracker):
    """A one-model chain does not record or consult skips, preserving single-model behavior."""
    generate._call_gemini_api.retry.wait = wait_none()
    mock_gemini_client.models.generate_content.side_effect = make_client_error_429("30s")

    test_id = f"single_{uuid4().hex}"
    with pytest.raises(QuotaExhausted) as exc_info:
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="hello",
            schema=DummyOutput,
            cache_key_parts=[test_id],
            models=["only-model"],
        )
    assert exc_info.value.retry_after_s == 30
    assert generate.get_skip_until("only-model") == 0.0


def test_fallback_on_429(monkeypatch, mock_gemini_client, cache_tracker):
    """First model returns 429; second model answers. Return model is second model."""
    generate._call_gemini_api.retry.wait = wait_none()

    def fake_generate(model, contents, config):
        if model == "model-1":
            raise make_client_error_429("45s")
        if model == "model-2":
            return MagicMock(text=json.dumps({"answer": "hello from model 2"}))
        raise RuntimeError("Unexpected model")

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    test_id = f"fallback_{uuid4().hex}"
    output, model_name = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="question",
        schema=DummyOutput,
        cache_key_parts=[test_id],
        models=["model-1", "model-2"],
    )

    assert output.answer == "hello from model 2"
    assert model_name == "model-2"
    assert generate.get_skip_until("model-1") > 0.0


def test_skip_until_in_future_and_expiry(monkeypatch, mock_gemini_client, cache_tracker):
    """Multi-model chain skips model while skip_until is in future; calls again after expiry."""
    generate._call_gemini_api.retry.wait = wait_none()

    current_time = 1000.0

    def fake_clock():
        return current_time

    monkeypatch.setattr(generate, "_clock", fake_clock)

    call_counts = {"m-1": 0, "m-2": 0}

    def fake_generate(model, contents, config):
        call_counts[model] += 1
        if model == "m-1":
            raise make_client_error_429("60s")
        return MagicMock(text=json.dumps({"answer": f"from {model}"}))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    # Request 1: m-1 raises 429, m-2 answers
    out1, m1 = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q1",
        schema=DummyOutput,
        cache_key_parts=[f"exp1_{uuid4().hex}"],
        models=["m-1", "m-2"],
    )
    assert m1 == "m-2"
    assert call_counts["m-1"] == 1
    assert call_counts["m-2"] == 1

    # Request 2: advance clock by 30 s (within 60 s window). m-1 should be skipped.
    current_time = 1030.0
    out2, m2 = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q2",
        schema=DummyOutput,
        cache_key_parts=[f"exp2_{uuid4().hex}"],
        models=["m-1", "m-2"],
    )
    assert m2 == "m-2"
    assert call_counts["m-1"] == 1  # Not called again
    assert call_counts["m-2"] == 2

    # Request 3: advance clock past 60 s (61 s later). m-1 is no longer skipped.
    current_time = 1061.0
    out3, m3 = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q3",
        schema=DummyOutput,
        cache_key_parts=[f"exp3_{uuid4().hex}"],
        models=["m-1", "m-2"],
    )
    assert m3 == "m-2"
    assert call_counts["m-1"] == 2  # Called again!
    assert call_counts["m-2"] == 3


def test_fallback_on_server_error(monkeypatch, mock_gemini_client, cache_tracker):
    """Model 1 raises 503 ServerError on all 3 attempts; Model 2 answers on attempt 1."""
    generate._call_gemini_api.retry.wait = wait_none()

    call_counts = {"m-1": 0, "m-2": 0}

    def fake_generate(model, contents, config):
        call_counts[model] += 1
        if model == "m-1":
            raise errors.ServerError(503, {"error": {"message": "Unavailable"}})
        return MagicMock(text=json.dumps({"answer": "from m-2"}))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    output, model_name = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q",
        schema=DummyOutput,
        cache_key_parts=[f"test_503_{uuid4().hex}"],
        models=["m-1", "m-2"],
    )
    assert output.answer == "from m-2"
    assert model_name == "m-2"
    assert call_counts["m-1"] == 3
    assert call_counts["m-2"] == 1


def test_all_models_429(monkeypatch, mock_gemini_client, cache_tracker):
    """When all models return 429, raise QuotaExhausted with min remaining retry_after_s."""
    generate._call_gemini_api.retry.wait = wait_none()

    def fake_generate(model, contents, config):
        if model == "m-1":
            raise make_client_error_429("60s")
        if model == "m-2":
            raise make_client_error_429("25s")
        raise RuntimeError()

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    with pytest.raises(QuotaExhausted) as exc_info:
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="q",
            schema=DummyOutput,
            cache_key_parts=[f"all_429_{uuid4().hex}"],
            models=["m-1", "m-2"],
        )
    assert exc_info.value.retry_after_s == 25


def test_one_429_one_unavailable(monkeypatch, mock_gemini_client, cache_tracker):
    """Mixed failure (429 and 503) raises ModelUnavailable (503)."""
    generate._call_gemini_api.retry.wait = wait_none()

    def fake_generate(model, contents, config):
        if model == "m-1":
            raise make_client_error_429("60s")
        if model == "m-2":
            raise errors.ServerError(503, {"error": {"message": "Service unavailable"}})
        raise RuntimeError()

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    with pytest.raises(ModelUnavailable):
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="q",
            schema=DummyOutput,
            cache_key_parts=[f"mixed_{uuid4().hex}"],
            models=["m-1", "m-2"],
        )


def test_client_error_stops_chain(monkeypatch, mock_gemini_client, cache_tracker):
    """A non-429 4xx ClientError reraises immediately without fallback."""
    generate._call_gemini_api.retry.wait = wait_none()

    call_counts = {"m-1": 0, "m-2": 0}

    def fake_generate(model, contents, config):
        call_counts[model] += 1
        if model == "m-1":
            raise errors.ClientError(400, {"error": {"message": "Bad request"}})
        return MagicMock(text="{}")

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    with pytest.raises(errors.ClientError) as exc_info:
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="q",
            schema=DummyOutput,
            cache_key_parts=[f"stop_400_{uuid4().hex}"],
            models=["m-1", "m-2"],
        )
    assert exc_info.value.code == 400
    assert call_counts["m-1"] == 1
    assert call_counts["m-2"] == 0


def test_cache_hit_under_second_model(mock_gemini_client, cache_tracker):
    """Pre-populated cache entry for second model returns that entry and model name."""
    db = get_db()
    cache_part = f"cache_part_{uuid4().hex}"
    prompt_str = "sys\n\nq_cache"
    prompt_hash = generate.hashlib.sha256(prompt_str.encode()).hexdigest()
    parts = ["m-2", "v1", prompt_hash, cache_part]
    key = generate.hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    cache_ref = db.document(llm_cache_path(key))
    cache_ref.set({
        "model": "m-2",
        "prompt_version": "v1",
        "output": {"answer": "cached answer"},
    })

    output, model_name = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q_cache",
        schema=DummyOutput,
        cache_key_parts=[cache_part],
        models=["m-1", "m-2"],
    )
    assert output.answer == "cached answer"
    assert model_name == "m-2"
    assert mock_gemini_client.models.generate_content.call_count == 0


def test_models_parameter_override(monkeypatch, mock_gemini_client, cache_tracker):
    """Explicit models argument takes precedence over configured chain."""
    generate._call_gemini_api.retry.wait = wait_none()

    called_models = []

    def fake_generate(model, contents, config):
        called_models.append(model)
        return MagicMock(text=json.dumps({"answer": "ok"}))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    output, model_name = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q",
        schema=DummyOutput,
        cache_key_parts=[f"override_{uuid4().hex}"],
        models=["custom-first", "custom-second"],
    )
    assert model_name == "custom-first"
    assert called_models == ["custom-first"]


def test_chain_budget_computations(monkeypatch, mock_gemini_client, cache_tracker):
    """Assert retry_with gets stop_before_delay(40) & HTTP timeout 40000ms; left < 1 stops chain."""
    generate._call_gemini_api.retry.wait = wait_none()

    # Part A: With clock leaving 40 s, HTTP timeout is 40000 ms and stop has stop_before_delay(40)
    current_time = 100.0

    def fake_clock_40():
        # Start at 100.0 (deadline = 250.0), then jump to 210.0 (left = 40.0)
        nonlocal current_time
        if current_time == 100.0:
            current_time = 210.0
            return 100.0
        return 210.0

    monkeypatch.setattr(generate, "_clock", fake_clock_40)

    captured_kwargs = {}
    orig_retry_with = generate._call_gemini_api.retry_with

    def spy_retry_with(**kwargs):
        captured_kwargs.update(kwargs)
        return orig_retry_with(**kwargs)

    monkeypatch.setattr(generate._call_gemini_api, "retry_with", spy_retry_with)

    captured_config = []

    def fake_generate(model, contents, config):
        captured_config.append(config)
        return MagicMock(text=json.dumps({"answer": "ok"}))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    output, model_name = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q",
        schema=DummyOutput,
        cache_key_parts=[f"budget_40_{uuid4().hex}"],
        models=["m-budget"],
    )
    assert model_name == "m-budget"
    assert len(captured_config) == 1
    assert captured_config[0].http_options.timeout == 40000

    # Verify stop_before_delay is part of stop predicate
    stop_pred = captured_kwargs.get("stop")
    assert any(
        isinstance(s, stop_before_delay) and s.max_delay == 39.0
        for s in stop_pred.stops
    )

    # Part B: With 0.5 s left, the chain stops without calling the model
    mock_gemini_client.models.generate_content.reset_mock()
    current_time_b = 100.0

    def fake_clock_05():
        nonlocal current_time_b
        if current_time_b == 100.0:
            current_time_b = 249.5
            return 100.0
        return 249.5

    monkeypatch.setattr(generate, "_clock", fake_clock_05)

    with pytest.raises(ModelUnavailable):
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="q",
            schema=DummyOutput,
            cache_key_parts=[f"budget_05_{uuid4().hex}"],
            models=["m-budget"],
        )
    assert mock_gemini_client.models.generate_content.call_count == 0


def test_later_attempt_gets_smaller_timeout(monkeypatch, mock_gemini_client, cache_tracker):
    """A retry attempt gets a smaller HTTP timeout than the initial attempt as clock advances."""
    generate._call_gemini_api.retry.wait = wait_none()

    current_time = 0.0

    def fake_clock():
        nonlocal current_time
        val = current_time
        if current_time == 0.0:
            # After start_time is sampled at 0.0 (deadline=150.0), jump to 100.0 (50.0s left)
            current_time = 100.0
        return val

    monkeypatch.setattr(generate, "_clock", fake_clock)

    captured_configs = []

    def fake_generate(model, contents, config):
        nonlocal current_time
        captured_configs.append(config)
        if len(captured_configs) == 1:
            # First attempt takes 15 s; advance clock and fail with 503 ServerError
            current_time += 15.0
            raise errors.ServerError(503, {"error": {"message": "Service unavailable"}})
        return MagicMock(text=json.dumps({"answer": "ok"}))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    output, model_name = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q",
        schema=DummyOutput,
        cache_key_parts=[f"smaller_timeout_{uuid4().hex}"],
        models=["m-timeout"],
    )

    assert output.answer == "ok"
    assert len(captured_configs) == 2
    assert captured_configs[0].http_options.timeout == 50000
    assert captured_configs[1].http_options.timeout == 35000
    assert captured_configs[1].http_options.timeout < captured_configs[0].http_options.timeout


def test_verify_scenario_deadline_budget_respected(monkeypatch, mock_gemini_client, cache_tracker):
    """Replay verify scenario (m-1: timeout, 503, 503; m-2: timeouts) and assert total <= 150s."""
    sim_time = 0.0

    def fake_now():
        return sim_time

    def fake_sleep(duration):
        nonlocal sim_time
        sim_time += duration

    monkeypatch.setattr(generate, "_clock", fake_now)
    monkeypatch.setattr("tenacity.time.monotonic", fake_now)
    monkeypatch.setattr("tenacity.nap.time.sleep", fake_sleep)

    calls = []

    def fake_generate(model, contents, config):
        nonlocal sim_time
        timeout_s = config.http_options.timeout / 1000.0
        calls.append((model, sim_time, timeout_s))
        if model == "m-1":
            if len([c for c in calls if c[0] == "m-1"]) == 1:
                sim_time += timeout_s
                raise TimeoutError("m-1 timed out")
            sim_time += 1.0
            raise errors.ServerError(503, {"error": {"message": "Service unavailable"}})
        if model == "m-2":
            sim_time += timeout_s
            raise TimeoutError("m-2 timed out")

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    with pytest.raises(ModelUnavailable):
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="q",
            schema=DummyOutput,
            cache_key_parts=[f"verify_scenario_{uuid4().hex}"],
            models=["m-1", "m-2"],
        )

    assert sim_time <= 150.0
    m1_calls = [c for c in calls if c[0] == "m-1"]
    m2_calls = [c for c in calls if c[0] == "m-2"]
    assert len(m1_calls) == 3
    assert len(m2_calls) >= 1


def test_schema_validation_failure_no_fallback(monkeypatch, mock_gemini_client, cache_tracker):
    """Schema validation failure twice raises UnreadableOutput without falling back."""
    generate._call_gemini_api.retry.wait = wait_none()

    called = []

    def fake_generate(model, contents, config):
        called.append(model)
        return MagicMock(text="not valid json")

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    with pytest.raises(UnreadableOutput):
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="q",
            schema=DummyOutput,
            cache_key_parts=[f"schema_fail_{uuid4().hex}"],
            models=["m-1", "m-2"],
        )
    assert called == ["m-1", "m-1"]  # Retried twice on m-1, never called m-2


def test_generate_json_thin_wrapper(monkeypatch, mock_gemini_client, cache_tracker):
    """generate_json returns only the validated schema object."""
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"answer": "wrapped"})
    )

    result = generate.generate_json(
        prompt_version="v1",
        system_instruction="sys",
        contents="q",
        schema=DummyOutput,
        cache_key_parts=[f"wrapper_{uuid4().hex}"],
        models=["m-1"],
    )
    assert isinstance(result, DummyOutput)
    assert result.answer == "wrapped"


def seed_ready_notebook_source(nb_id: str, token: str) -> None:
    db = get_db()
    src_id = f"src_{uuid4().hex[:6]}"
    nb_ref = db.document(notebook_path(nb_id))
    nb_ref.update({
        "sources_summary": [
            {
                "source_id": src_id,
                "ref_n": 1,
                "title": "Topic Doc",
                "kind": "pdf",
                "status": "ready",
            }
        ],
        "status": "ready",
    })
    c_ref = db.document(chunk_path(nb_id, f"{src_id}-00000"))
    c_ref.set({
        "source_id": src_id,
        "kind": "text",
        "text": "Bayes theorem conditional probability",
        "loc": {"source_id": src_id, "page": 1, "page_label": None, "bbox": None},
        "topic_id": None,
        "token_count": 5,
        "image_path": None,
    })


def test_ask_fallback_returns_second_model(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """When /ask encounters 429 on primary model, it returns the fallback model that answered."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fallback Ask Test"},
    ).json()["id"]
    notebook_tracker.append(nb_id)
    seed_ready_notebook_source(nb_id, token)

    settings = get_settings()
    primary = settings.gemini_model
    fallback = f"fallback_{uuid4().hex[:6]}"

    orig_fallback = settings.gemini_fallback_models
    try:
        settings.gemini_fallback_models = fallback

        def fake_generate(model, contents, config):
            if model == primary:
                raise make_client_error_429("50s")
            if model == fallback:
                return MagicMock(
                    text=json.dumps({
                        "paragraphs": [
                            {"text": "Answer from fallback model", "sources": [1]}
                        ]
                    })
                )
            raise RuntimeError(f"Unexpected model: {model}")

        mock_gemini_client.models.generate_content.side_effect = fake_generate

        res = client.post(
            f"/v1/notebooks/{nb_id}/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": f"What is conditional probability? {uuid4().hex[:6]}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["model"] == fallback
        assert len(data["paragraphs"]) > 0
        assert data["paragraphs"][0]["text"] == "Answer from fallback model"
    finally:
        settings.gemini_fallback_models = orig_fallback


def test_ask_cache_hit_returns_cached_model(
    user_tracker, notebook_tracker, cache_tracker, mock_gemini_client
):
    """When /ask hits a cached response under a fallback model, it returns that cached model."""
    generate._call_gemini_api.retry.wait = wait_none()

    uid, token = create_emulator_user()
    user_tracker.append(uid)
    nb_id = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Cache Ask Test"},
    ).json()["id"]
    notebook_tracker.append(nb_id)
    seed_ready_notebook_source(nb_id, token)

    settings = get_settings()
    primary = settings.gemini_model
    fallback = f"cached_{uuid4().hex[:6]}"

    orig_fallback = settings.gemini_fallback_models
    try:
        settings.gemini_fallback_models = fallback
        question = f"What is Bayes theorem? {uuid4().hex[:6]}"

        def fake_generate(model, contents, config):
            if model == primary:
                raise make_client_error_429("50s")
            if model == fallback:
                return MagicMock(
                    text=json.dumps({
                        "paragraphs": [
                            {"text": "Cached answer from fallback", "sources": [1]}
                        ]
                    })
                )
            raise RuntimeError(f"Unexpected model: {model}")

        mock_gemini_client.models.generate_content.side_effect = fake_generate

        # Request 1: primary 429, fallback responds and writes cache under fallback
        res1 = client.post(
            f"/v1/notebooks/{nb_id}/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": question},
        )
        assert res1.status_code == 200
        assert res1.json()["model"] == fallback

        # Now configure mock so ANY call to Gemini API raises RuntimeError
        mock_gemini_client.models.generate_content.side_effect = RuntimeError(
            "Gemini called during cache hit"
        )
        mock_gemini_client.models.generate_content.reset_mock()

        # Request 2: identical question hits cache under fallback model
        res2 = client.post(
            f"/v1/notebooks/{nb_id}/ask",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": question},
        )
        assert res2.status_code == 200
        data = res2.json()
        assert data["model"] == fallback
        assert data["paragraphs"][0]["text"] == "Cached answer from fallback"
        assert mock_gemini_client.models.generate_content.call_count == 0
    finally:
        settings.gemini_fallback_models = orig_fallback
