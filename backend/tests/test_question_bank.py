import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from google.genai import _transformers as genai_transformers
from google.genai import errors
from pydantic import BaseModel

from app.chat.answer import AskGeminiOutput
from app.db import chunk_path, get_db, notebook_path
from app.db.notebooks import (
    get_notebook_snapshot,
    rebuild_notebook_status_and_summary,
    set_questions_verified,
)
from app.db.questions import (
    delete_stale_questions,
    get_question,
    list_verified_questions,
    put_question,
)
from app.db.sources import get_chunks_by_ids
from app.db.topics import write_topics_batch
from app.llm import generate
from app.llm.prompts.questions import (
    MCQ_PROMPT_VERSION,
    NUMERICAL_PROMPT_VERSION,
    BlindMCQBatch,
    GeneratedMCQItem,
    GeneratedMCQOption,
    MCQGenerationBatch,
    NumericalGenerationBatch,
    NumericalRetryBatch,
    build_blind_mcq_prompt,
    build_mcq_gen_prompt,
    build_numerical_gen_prompt,
    build_numerical_retry_prompt,
)
from app.models.question import QuestionAnswer, QuestionOption, QuestionVerification, StoredQuestion
from app.questions.bank import (
    BudgetExhausted,
    process_mcq_batch,
    process_numerical_batch,
    shuffle_mcq_options,
    validate_mcq_item,
)
from app.questions.numerical import SNIPPET_RULES
from app.questions.passages import select_topic_passages
from scripts import build_question_bank as bqb_script


@pytest.fixture(autouse=True)
def isolate_course_dir(monkeypatch, tmp_path):
    """Keep script report files out of the real course folder."""
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("COURSE_DATA_DIR", str(tmp_path))
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def setup_fallback_models(monkeypatch):
    """Ensure at least 2 models are in the chain so MCQ blind check safety checks pass."""
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "gemini_fallback_models", "gemini-3.5-flash,gemini-3.1-flash-lite"
    )


def seed_test_notebook(nb_id: str) -> None:
    """Seed test notebook with sources summary, topic, and chunks."""
    db = get_db()
    src_sol = "src_r01_sol"
    src_textbook = "src_textbook"
    src_video = "src_l01"

    db.document(notebook_path(nb_id)).set({
        "name": "Test NB",
        "owner_uid": "test-owner",
        "is_demo": False,
        "status": "ready",
        "sources_summary": [
            {
                "source_id": src_sol, "ref_n": 1, "title": "R01 Sol",
                "kind": "pdf", "status": "ready"
            },
            {
                "source_id": src_textbook, "ref_n": 2, "title": "Textbook",
                "kind": "pdf", "status": "ready"
            },
            {
                "source_id": src_video, "ref_n": 3, "title": "L01 Video",
                "kind": "video", "status": "ready"
            },
        ],
        "counts": {"chunks": 3, "items": 10},
    })

    # Add chunks
    c1_id = f"{src_sol}-00000"
    c2_id = f"{src_textbook}-00000"
    c3_id = f"{src_video}-00000"

    db.document(chunk_path(nb_id, c1_id)).set({
        "source_id": src_sol,
        "text": "Bayes theorem formula P(A|B) = P(B|A)P(A)/P(B).",
        "loc": {"source_id": src_sol, "page": 1, "page_label": "1"},
        "token_count": 10,
        "embedding": [0.1, 0.2, 0.3],
    })
    db.document(chunk_path(nb_id, c2_id)).set({
        "source_id": src_textbook,
        "text": "Conditioning partitions the sample space into disjoint events.",
        "loc": {"source_id": src_textbook, "page": 70, "page_label": "70"},
        "token_count": 12,
        "embedding": [0.2, 0.3, 0.4],
    })
    db.document(chunk_path(nb_id, c3_id)).set({
        "source_id": src_video,
        "text": "Lecture on probability models and sample spaces.",
        "loc": {"source_id": src_video, "t_start_s": 120.0, "t_end_s": 240.0},
        "token_count": 10,
        "embedding": [0.3, 0.4, 0.5],
    })

    # Write topic t1
    topics = [
        {
            "id": "t1",
            "name": "Probability Models",
            "order": 1,
            "summary": "Sample spaces and axioms",
            "prerequisite_ids": [],
            "is_other": False,
            "locations": [
                {"chunk_id": c1_id, "loc": {"source_id": src_sol, "page": 1, "page_label": "1"}},
                {
                    "chunk_id": c2_id,
                    "loc": {"source_id": src_textbook, "page": 70, "page_label": "70"},
                },
                {
                    "chunk_id": c3_id,
                    "loc": {"source_id": src_video, "t_start_s": 120.0, "t_end_s": 240.0},
                },
            ],
            "location_count": 3,
        }
    ]
    write_topics_batch(nb_id, topics)


def test_snippet_rules_verbatim_in_numerical_prompt():
    """Verify SNIPPET_RULES is inserted verbatim via f-string in numerical gen and retry prompts."""
    sys_gen, _ = build_numerical_gen_prompt("t1", "Topic", "Summary", "Passages")
    assert SNIPPET_RULES in sys_gen

    sys_retry, _ = build_numerical_retry_prompt("t1", [], "Passages")
    assert SNIPPET_RULES in sys_retry


def test_passage_selection_deterministic_and_no_embedding(notebook_tracker):
    """Verify passage selection is deterministic and get_chunks_by_ids omits embedding."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    # First call
    p1 = select_topic_passages(nb_id, "t1", "mcq")
    # Second call
    p2 = select_topic_passages(nb_id, "t1", "mcq")
    assert [p.chunk_id for p in p1] == [p.chunk_id for p in p2]
    assert len(p1) == 3

    # Field mask check
    chunks_dict = get_chunks_by_ids(nb_id, [p1[0].chunk_id])
    assert p1[0].chunk_id in chunks_dict
    cdata = chunks_dict[p1[0].chunk_id]
    assert "embedding" not in cdata
    assert "text" in cdata
    assert "token_count" in cdata


def test_mcq_invalid_items_rejected_with_reasons():
    """Verify bad options, duplicate stems, long stems, or bad sources fail validation."""
    seen_stems: set[str] = set()

    # 1. Valid 4 options
    item_valid = GeneratedMCQItem(
        difficulty=1,
        stem="Short stem",
        options=[
            GeneratedMCQOption(text="A", misconception=""),
            GeneratedMCQOption(text="B", misconception=""),
            GeneratedMCQOption(text="C", misconception=""),
            GeneratedMCQOption(text="D", misconception=""),
        ],
        correct_index=0,
        explanation="Exp",
        sources=[1],
    )
    ok, msg = validate_mcq_item(item_valid, 3, seen_stems)
    assert ok is True

    # 2. Duplicate option texts
    item_dup_opts = GeneratedMCQItem(
        difficulty=1,
        stem="Unique stem 1",
        options=[
            GeneratedMCQOption(text="Same", misconception=""),
            GeneratedMCQOption(text="same ", misconception=""),
            GeneratedMCQOption(text="C", misconception=""),
            GeneratedMCQOption(text="D", misconception=""),
        ],
        correct_index=0,
        explanation="Exp",
        sources=[1],
    )
    ok, msg = validate_mcq_item(item_dup_opts, 3, seen_stems)
    assert ok is False
    assert "duplicate option" in msg

    # 3. Stem > 800 characters
    item_long_stem = GeneratedMCQItem(
        difficulty=1,
        stem="x" * 801,
        options=[GeneratedMCQOption(text=str(i), misconception="") for i in range(4)],
        correct_index=0,
        explanation="Exp",
        sources=[1],
    )
    ok, msg = validate_mcq_item(item_long_stem, 3, seen_stems)
    assert ok is False
    assert "stem exceeds 800" in msg

    # 4. Duplicate stem
    seen_stems.add("duplicate stem")
    item_dup_stem = GeneratedMCQItem(
        difficulty=1,
        stem="Duplicate Stem ",
        options=[GeneratedMCQOption(text=str(i), misconception="") for i in range(4)],
        correct_index=0,
        explanation="Exp",
        sources=[1],
    )
    ok, msg = validate_mcq_item(item_dup_stem, 3, seen_stems)
    assert ok is False
    assert "duplicate stem" in msg

    # 5. Out of range source number
    item_bad_src = GeneratedMCQItem(
        difficulty=1,
        stem="Valid stem",
        options=[GeneratedMCQOption(text=str(i), misconception="") for i in range(4)],
        correct_index=0,
        explanation="Exp",
        sources=[99],
    )
    ok, msg = validate_mcq_item(item_bad_src, 3, set())
    assert ok is False
    assert "no valid source" in msg


def test_mcq_option_shuffle_deterministic_and_key_varies():
    """Verify options shuffle deterministically and key across 8 questions is not fixed."""
    keys = []
    opts = [GeneratedMCQOption(text=f"Option {i}", misconception="") for i in range(4)]

    for q_idx in range(1, 9):
        qid = f"t1-mcq-v{MCQ_PROMPT_VERSION}-{q_idx:02d}"
        shuffled1, key1 = shuffle_mcq_options(qid, opts, correct_index=0)
        shuffled2, key2 = shuffle_mcq_options(qid, opts, correct_index=0)
        assert [o.text for o in shuffled1] == [o.text for o in shuffled2]
        assert key1 == key2
        keys.append(key1)

    # Key should vary across 8 questions (not all identical letters)
    assert len(set(keys)) > 1


def test_mcq_blind_check_pass_and_mismatch(notebook_tracker, mock_gemini_client, cache_tracker):
    """Verify blind check match marks verified and mismatch marks rejected."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    passages = select_topic_passages(nb_id, "t1", "mcq")

    # Generation response (2 items)
    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Question 1 stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 2,
                "stem": "Question 2 stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 1,
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })

    # Shuffled key for Q1
    raw_opts = [GeneratedMCQOption(text=f"Opt {i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)
    # Wrong key for Q2
    wrong_key = "d" if q1_key != "d" else "a"

    blind_json = json.dumps({
        "answers": [
            {"question_index": 1, "selected_option_id": q1_key},
            {"question_index": 2, "selected_option_id": wrong_key},
        ]
    })

    def fake_generate(model, contents, config):
        if "blind" in model or "m-fallback" in model:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="Topic 1",
        topic_summary="Summary",
        passages=passages,
        count=2,
        configured_chain=["m-primary", "m-fallback"],
    )

    assert len(stored) == 2
    q1 = stored[0][1]
    q2 = stored[1][1]

    assert q1.status == "verified"
    assert q1.verification.passed is True
    assert "ok:" in q1.verification.detail

    assert q2.status == "rejected"
    assert q2.verification.passed is False
    assert "mismatch:" in q2.verification.detail


def test_mcq_blind_call_models_excludes_generator_model(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """Verify blind check invocation models= excludes the generating model."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    passages = select_topic_passages(nb_id, "t1", "mcq")
    called_models = []

    def fake_generate(model, contents, config):
        called_models.append(model)
        if model == "m-gen":
            return MagicMock(text=json.dumps({
                "questions": [
                    {
                        "difficulty": 1,
                        "stem": "Stem?",
                        "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                        "correct_index": 0,
                        "explanation": "Exp",
                        "sources": [1],
                    }
                ]
            }))
        ans_payload = {"answers": [{"question_index": 1, "selected_option_id": "a"}]}
        return MagicMock(text=json.dumps(ans_payload))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-gen", "m-blind"],
    )

    assert called_models[0] == "m-gen"
    assert called_models[1] == "m-blind"


def test_mcq_blind_prompt_omits_key_explanation_misconception():
    """Verify blind check prompt contains stem and options without key/explanation/misconception."""
    q_data = [
        {
            "question_index": 1,
            "stem": "What is probability?",
            "options": [{"id": "a", "text": "A number"}, {"id": "b", "text": "A string"}],
        }
    ]
    sys_inst, contents = build_blind_mcq_prompt(q_data, "Passage text")
    assert "What is probability?" in contents
    assert "a) A number" in contents
    assert "correct_index" not in contents
    assert "explanation" not in contents
    assert "misconception" not in contents


def test_numerical_pass_stores_computed_value(notebook_tracker, mock_gemini_client, cache_tracker):
    """Verify valid numerical snippet stores canonical computed value and verified status."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "numerical")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 3/8 as exact fraction.",
                "stated_answer": "3/8",
                "solution_code": "answer = Fraction(3, 8)",
                "explanation": "Direct calculation.",
                "sources": [1],
            }
        ]
    })
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(text=gen_json)

    stored, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-primary"],
    )

    assert len(stored) == 1
    qid, q = stored[0]
    assert q.status == "verified"
    assert q.answer.value == "3/8"
    assert q.solution_code == "answer = Fraction(3, 8)"
    assert q.verification.passed is True


def test_numerical_fail_then_retry_pass(notebook_tracker, mock_gemini_client, cache_tracker):
    """Verify item failing first run is included in retry and passes if retried code works."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "numerical")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 5/2.",
                "stated_answer": "5/2",
                "solution_code": "answer = 2  # wrong value",
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    retry_json = json.dumps({
        "items": [
            {
                "index": 1,
                "stated_answer": "5/2",
                "solution_code": "answer = Fraction(5, 2)",
                "explanation": "Corrected",
            }
        ]
    })

    calls = 0

    def fake_generate(model, contents, config):
        nonlocal calls
        calls += 1
        return MagicMock(text=gen_json if calls == 1 else retry_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-primary"],
    )

    assert calls == 2
    q = stored[0][1]
    assert q.status == "verified"
    assert q.answer.value == "5/2"
    assert q.solution_code == "answer = Fraction(5, 2)"


def test_numerical_fail_twice_rejected_with_both_details(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """Verify item failing gen and retry is rejected with combined details."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "numerical")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 1/2.",
                "stated_answer": "1/2",
                "solution_code": "answer = 1",
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    retry_json = json.dumps({
        "items": [
            {
                "index": 1,
                "stated_answer": "1/2",
                "solution_code": "answer = 2",
                "explanation": "Still wrong",
            }
        ]
    })

    calls = 0

    def fake_generate(model, contents, config):
        nonlocal calls
        calls += 1
        return MagicMock(text=gen_json if calls == 1 else retry_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-primary"],
    )

    q = stored[0][1]
    assert q.status == "rejected"
    assert q.verification.passed is False
    assert "| retry:" in q.verification.detail


def test_numerical_exactly_one_retry_call_per_topic(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """Verify multiple failing items in a topic trigger exactly one combined retry call."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "numerical")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Q1",
                "stated_answer": "1/2",
                "solution_code": "answer = 0",
                "explanation": "Exp",
                "sources": [1],
            },
            {
                "difficulty": 2,
                "stem": "Q2",
                "stated_answer": "1/3",
                "solution_code": "answer = 0",
                "explanation": "Exp",
                "sources": [1],
            },
        ]
    })
    retry_json = json.dumps({
        "items": [
            {
                "index": 1, "stated_answer": "1/2",
                "solution_code": "answer = Fraction(1, 2)", "explanation": "ok"
            },
            {
                "index": 2, "stated_answer": "1/3",
                "solution_code": "answer = Fraction(1, 3)", "explanation": "ok"
            },
        ]
    })

    calls = 0

    def fake_generate(model, contents, config):
        nonlocal calls
        calls += 1
        return MagicMock(text=gen_json if calls == 1 else retry_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=2,
        configured_chain=["m-primary"],
    )

    assert calls == 2  # exactly 1 generation + 1 retry
    assert stored[0][1].status == "verified"
    assert stored[1][1].status == "verified"


def test_budget_hook_server_error_twice_budget_2_raises_budget_exhausted(mock_gemini_client):
    """Verify ServerError twice with budget 2 raises BudgetExhausted through real chain."""
    tracker = bqb_script.BudgetTracker(max_calls=2, min_interval_s=0.0)

    # ServerError on attempts
    mock_gemini_client.models.generate_content.side_effect = errors.ServerError(
        503, {"error": {"message": "Service Unavailable"}}
    )

    class Dummy(BaseModel):
        val: str

    with pytest.raises(BudgetExhausted):
        generate.generate_json_with_model(
            prompt_version="v1",
            system_instruction="sys",
            contents="contents",
            schema=Dummy,
            cache_key_parts=[f"budget_{uuid4().hex}"],
            models=["m-test"],
            on_attempt=tracker.on_attempt,
        )

    assert tracker.attempts == 2


def test_budget_hook_cache_hit_never_calls_hook(mock_gemini_client, cache_tracker):
    """Verify a cache hit never calls the attempt hook and costs 0 attempts."""
    tracker = bqb_script.BudgetTracker(max_calls=5, min_interval_s=0.0)

    class Dummy(BaseModel):
        val: str

    key_parts = [f"cache_hit_{uuid4().hex}"]

    # 1. First call writes cache
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"val": "hello"})
    )
    out1, m1 = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="contents",
        schema=Dummy,
        cache_key_parts=key_parts,
        models=["m-test"],
        on_attempt=tracker.on_attempt,
    )
    assert tracker.attempts == 1

    # 2. Second call should hit cache without calling hook
    out2, m2 = generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="contents",
        schema=Dummy,
        cache_key_parts=key_parts,
        models=["m-test"],
        on_attempt=tracker.on_attempt,
    )
    assert out2.val == "hello"
    assert tracker.attempts == 1  # unchanged!


def test_generate_json_with_model_max_output_tokens(mock_gemini_client, cache_tracker):
    """Verify default max_output_tokens is 2000 and explicit value passes to config."""
    class Dummy(BaseModel):
        val: str

    captured_configs = []

    def fake_generate(model, contents, config):
        captured_configs.append(config)
        return MagicMock(text=json.dumps({"val": "ok"}))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    # Call with default
    generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q1",
        schema=Dummy,
        cache_key_parts=[f"tok_def_{uuid4().hex}"],
        models=["m-test"],
    )
    assert captured_configs[0].max_output_tokens == 2000

    # Call with 8192
    generate.generate_json_with_model(
        prompt_version="v1",
        system_instruction="sys",
        contents="q2",
        schema=Dummy,
        cache_key_parts=[f"tok_exp_{uuid4().hex}"],
        models=["m-test"],
        max_output_tokens=8192,
    )
    assert captured_configs[1].max_output_tokens == 8192


def test_quota_exhausted_stops_run_exit_1(notebook_tracker, mock_gemini_client, tmp_path):
    """Verify QuotaExhausted stops run, writes report.json for first batch, and exits 1."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)
    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "What is prob?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "numerical" in sys_inst.lower() or "numerical" in contents.lower():
            raise errors.ClientError(429, {"error": {"code": 429, "message": "Resource Exhausted"}})
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=1,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert exit_code == 1

    report_file = tmp_path / "derived" / "question_bank" / "report.json"
    assert report_file.exists()
    rep = json.loads(report_file.read_text(encoding="utf-8"))
    assert rep["status"] == "quota_exhausted"
    assert len(rep["batches"]) == 2
    assert rep["batches"][0]["type"] == "mcq"
    assert rep["batches"][0]["verified"] == 1
    assert "QuotaExhausted" in rep["batches"][1]["error"]

    nb_doc = get_notebook_snapshot(nb_id).to_dict()
    assert nb_doc["counts"]["questions_verified"] == 1


def test_budget_exhausted_stops_run_exit_3(notebook_tracker, mock_gemini_client, tmp_path):
    """Verify BudgetExhausted stops run, writes report.json for first batch, and exits 3."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)
    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "What is prob?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=1,
        max_calls=2,
        min_interval_s=0.0,
    )
    assert exit_code == 3

    report_file = tmp_path / "derived" / "question_bank" / "report.json"
    assert report_file.exists()
    rep = json.loads(report_file.read_text(encoding="utf-8"))
    assert rep["status"] == "budget_exhausted"
    assert len(rep["batches"]) == 2
    assert rep["batches"][0]["verified"] == 1
    assert "BudgetExhausted" in rep["batches"][1]["error"]

    nb_doc = get_notebook_snapshot(nb_id).to_dict()
    assert nb_doc["counts"]["questions_verified"] == 1


def test_model_unavailable_skips_batch_and_continues(
    notebook_tracker, mock_gemini_client, cache_tracker, tmp_path
):
    """Verify ModelUnavailable in one batch skips it and subsequent batches are written."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    calls = 0

    def fake_generate(model, contents, config):
        nonlocal calls
        calls += 1
        if "mcq" in contents:
            raise errors.ServerError(503, {"error": {"message": "Unavailable"}})
        # Numerical batch succeeds
        return MagicMock(text=json.dumps({
            "questions": [
                {
                    "difficulty": 1,
                    "stem": "Compute 1.",
                    "stated_answer": "1",
                    "solution_code": "answer = 1",
                    "explanation": "Exp",
                    "sources": [1],
                }
            ]
        }))

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=1,
        max_calls=20,
        min_interval_s=0.0,
    )
    assert exit_code == 1  # had model error

    # MCQ was skipped, but numerical was written!
    q = get_question(nb_id, f"t1-numerical-v{NUMERICAL_PROMPT_VERSION}-01")
    assert q is not None
    assert q.status == "verified"

    assert (tmp_path / "derived" / "question_bank" / "report.json").exists()
    assert (tmp_path / "derived" / "question_bank" / "questions.md").exists()


def test_failed_numerical_retry_still_writes_batch(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """Verify numerical retry failure still writes batch with first-round items verified."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    # 2 questions: Q1 passes first try, Q2 fails and needs retry
    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 1.",
                "stated_answer": "1",
                "solution_code": "answer = 1",
                "explanation": "ok",
                "sources": [1],
            },
            {
                "difficulty": 2,
                "stem": "Compute 2.",
                "stated_answer": "2",
                "solution_code": "answer = 0  # wrong",
                "explanation": "fail",
                "sources": [1],
            },
        ]
    })

    calls = 0

    def fake_generate(model, contents, config):
        nonlocal calls
        calls += 1
        if calls == 1:
            return MagicMock(text=gen_json)
        # Retry call raises ServerError (ModelUnavailable)
        raise errors.ServerError(503, {"error": {"message": "Retry model down"}})

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=select_topic_passages(nb_id, "t1", "numerical"),
        count=2,
        configured_chain=["m-test"],
    )

    assert len(stored) == 2
    q1 = stored[0][1]
    q2 = stored[1][1]

    assert q1.status == "verified"
    assert q2.status == "rejected"
    assert "retry: call failed: ModelUnavailable" in q2.verification.detail


def test_rerun_zero_gemini_calls_and_identical_ids(
    notebook_tracker, mock_gemini_client, cache_tracker, tmp_path
):
    """Verify re-run makes zero Gemini calls and yields identical question IDs."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)

    mcq_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "What is prob?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    mcq_blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    num_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 1.",
                "stated_answer": "1",
                "solution_code": "answer = 0",
                "explanation": "init fail",
                "sources": [1],
            }
        ]
    })
    num_retry_json = json.dumps({
        "items": [
            {
                "index": 1,
                "stated_answer": "1",
                "solution_code": "answer = 1",
                "explanation": "fixed",
            }
        ]
    })

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=mcq_blind_json)
        if "correcting solution snippets" in sys_inst.lower() or "retrying" in sys_inst.lower():
            return MagicMock(text=num_retry_json)
        if "numerical" in sys_inst.lower() or "numerical" in contents.lower():
            return MagicMock(text=num_gen_json)
        return MagicMock(text=mcq_gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    # Run 1
    code1 = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=1,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert code1 == 0
    initial_call_count = mock_gemini_client.models.generate_content.call_count

    assert (tmp_path / "derived" / "question_bank" / "report.json").exists()
    assert (tmp_path / "derived" / "question_bank" / "questions.md").exists()

    # Run 2: mock fails if called
    mock_gemini_client.models.generate_content.side_effect = RuntimeError(
        "Called during cached rerun!"
    )

    code2 = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=1,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert code2 == 0
    # No new calls
    assert mock_gemini_client.models.generate_content.call_count == initial_call_count


def test_stale_question_deletion_scoped_to_topic_and_type(notebook_tracker):
    """Verify delete_stale_questions deletes only old questions of same topic and type."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    now = datetime.now(UTC)
    q_dummy = StoredQuestion(
        type="mcq",
        topic_id="t1",
        difficulty=1,
        stem="Old stem",
        options=[QuestionOption(id=c, text=c) for c in "abcd"],
        answer=QuestionAnswer(option_id="a"),
        explanation="Exp",
        verification=QuestionVerification(method="blind_mcq", passed=True, detail="ok"),
        status="verified",
        batch_id="t1-mcq-v0",
        created_at=now,
    )
    # Stale question in t1 mcq
    put_question(nb_id, "t1-mcq-v0-01", q_dummy)

    # Different type in t1 (numerical)
    q_num = StoredQuestion(
        type="numerical",
        topic_id="t1",
        difficulty=1,
        stem="Num stem",
        options=[],
        answer=QuestionAnswer(value="1"),
        explanation="Exp",
        verification=QuestionVerification(method="python", passed=True, detail="ok"),
        status="verified",
        batch_id="t1-numerical-v0",
        created_at=now,
    )
    put_question(nb_id, "t1-numerical-v0-01", q_num)

    # Different topic in mcq (t2)
    q_t2 = StoredQuestion(
        type="mcq",
        topic_id="t2",
        difficulty=1,
        stem="T2 stem",
        options=[QuestionOption(id=c, text=c) for c in "abcd"],
        answer=QuestionAnswer(option_id="a"),
        explanation="Exp",
        verification=QuestionVerification(method="blind_mcq", passed=True, detail="ok"),
        status="verified",
        batch_id="t2-mcq-v0",
        created_at=now,
    )
    put_question(nb_id, "t2-mcq-v0-01", q_t2)

    # Active t1 mcq question
    put_question(nb_id, f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", q_dummy)

    # Delete stale for t1 mcq keeping only t1-mcq-v1-01
    deleted = delete_stale_questions(nb_id, "t1", "mcq", {f"t1-mcq-v{MCQ_PROMPT_VERSION}-01"})
    assert deleted == 1

    assert get_question(nb_id, "t1-mcq-v0-01") is None
    assert get_question(nb_id, f"t1-mcq-v{MCQ_PROMPT_VERSION}-01") is not None
    assert get_question(nb_id, "t1-numerical-v0-01") is not None
    assert get_question(nb_id, "t2-mcq-v0-01") is not None


def test_counts_questions_verified_set_without_touching_chunks(notebook_tracker):
    """Verify set_questions_verified updates counts without touching counts.chunks."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    db = get_db()
    rebuild_notebook_status_and_summary(
        nb=nb_id,
        sources_summary=[
            {"source_id": "src_1", "ref_n": 1, "title": "S1", "kind": "pdf", "status": "ready"}
        ],
        total_chunks=123,
    )

    set_questions_verified(nb_id, 45)

    doc = db.document(notebook_path(nb_id)).get().to_dict() or {}
    counts = doc.get("counts", {})
    assert counts.get("chunks") == 123
    assert counts.get("questions_verified") == 45
    assert "items" not in counts


def test_emulator_guard_exits_2(monkeypatch):
    """Verify script exits 2 if safety preconditions fail."""
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "production-project")
    with pytest.raises(SystemExit) as excinfo:
        bqb_script.build_question_bank(
            notebook_id="any",
            topics=["t1"],
            types=["mcq"],
            per_type=1,
            max_calls=10,
            min_interval_s=0.0,
        )
    assert excinfo.value.code == 2


def test_dry_run_zero_calls_and_zero_writes(notebook_tracker, mock_gemini_client, capsys):
    """Verify --dry-run prints planned passages and calls with zero API calls and zero writes."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=8,
        max_calls=40,
        min_interval_s=0.0,
        dry_run=True,
    )
    assert code == 0
    assert mock_gemini_client.models.generate_content.call_count == 0

    captured = capsys.readouterr().out
    assert "DRY RUN" in captured
    assert "Topic t1 (mcq)" in captured
    assert "Topic t1 (numerical)" in captured
    assert "page 1" in captured
    assert "tokens" in captured
    assert "src_r01_sol-00000" in captured


def test_mcq_shuffle_key_points_at_correct_text():
    """Verify shuffle-to-key mapping matches generator correct_index across batch."""
    # 1. Pinned hand-computed case
    opts = [GeneratedMCQOption(text=f"raw{i}", misconception="") for i in range(4)]
    shuffled, key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", opts, correct_index=0)
    assert key == "d"
    assert [o.text for o in shuffled] == ["raw2", "raw3", "raw1", "raw0"]

    # 2. Check all 8 questions in batch where correct_index varies
    for idx in range(1, 9):
        ci = (idx - 1) % 4
        qid = f"t1-mcq-v{MCQ_PROMPT_VERSION}-{idx:02d}"
        raw = [GeneratedMCQOption(text=f"option_{idx}_{i}", misconception="") for i in range(4)]
        shuffled_opts, assigned_key = shuffle_mcq_options(qid, raw, correct_index=ci)
        opt_for_key = next(o for o in shuffled_opts if o.id == assigned_key)
        assert opt_for_key.text == raw[ci].text


def test_chunk_read_field_mask_excludes_embedding(notebook_tracker):
    """Verify get_chunks_by_ids field_paths mask excludes embedding field."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    db = get_db()
    cid = "chunk_with_emb"
    db.document(chunk_path(nb_id, cid)).set({
        "text": "Mask test text",
        "token_count": 4,
        "embedding": [0.1, 0.2, 0.3],
    })

    data = get_chunks_by_ids(nb_id, [cid], field_paths=("text", "token_count"))
    assert cid in data
    assert set(data[cid].keys()) == {"text", "token_count"}
    assert "embedding" not in data[cid]


def test_select_passages_masks_chunk_reads(notebook_tracker, monkeypatch):
    """Verify select_topic_passages requests only text and token_count fields."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    import app.questions.passages as passages_mod

    original_get = passages_mod.get_chunks_by_ids
    captured_field_paths = []

    def spy_get_chunks(nb, chunk_ids, field_paths=("text", "token_count")):
        captured_field_paths.append(field_paths)
        return original_get(nb, chunk_ids, field_paths)

    monkeypatch.setattr(passages_mod, "get_chunks_by_ids", spy_get_chunks)

    passages = passages_mod.select_topic_passages(nb_id, "t1", "mcq")
    assert len(passages) > 0
    assert len(captured_field_paths) == 1
    assert captured_field_paths[0] == ("text", "token_count")


def test_numerical_verified_value_is_computed_canonical(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """Verify numerical answer is stored as computed value in lowest terms, not stated string."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    passages = select_topic_passages(nb_id, "t1", "numerical")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 2/4 in reduced terms.",
                "stated_answer": "2/4",
                "solution_code": "answer = Fraction(1, 2)",
                "explanation": "2/4 reduces to 1/2.",
                "sources": [1],
            }
        ]
    })
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(text=gen_json)

    stored_pairs, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-primary"],
    )
    assert len(stored_pairs) == 1
    qid, q_doc = stored_pairs[0]
    assert q_doc.status == "verified"
    assert q_doc.answer.value == "1/2"


def test_no_citation_items_rejected(
    notebook_tracker, mock_gemini_client, cache_tracker, monkeypatch
):
    """Verify items with unresolvable citations are rejected and not listed as verified."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    passages = select_topic_passages(nb_id, "t1", "mcq")

    # MCQ with valid source number [1], but resolve_question_citations mocked to return []
    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)

    mcq_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "MCQ bad source stem.",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=blind_json)
        return MagicMock(text=mcq_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate
    monkeypatch.setattr("app.questions.bank.resolve_question_citations", lambda *args, **kwargs: [])

    mcq_pairs, _ = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-primary", "m-fallback"],
    )
    for qid, q in mcq_pairs:
        put_question(nb_id, qid, q)
        assert q.status == "rejected"
        assert q.verification.detail == "rejected: no valid citations"

    # Numerical with invalid sources [99]
    num_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Numerical bad source stem.",
                "stated_answer": "1",
                "solution_code": "answer = 1",
                "explanation": "Exp",
                "sources": [99],
            }
        ]
    })
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(text=num_json)

    num_pairs, _ = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-primary"],
    )
    for qid, q in num_pairs:
        put_question(nb_id, qid, q)
        assert q.status == "rejected"
        assert "no valid citations" in q.verification.detail.lower()

    verified_qs = list_verified_questions(nb_id, "t1")
    assert len(verified_qs) == 0


def test_script_stale_delete_keeps_other_type_and_topic(notebook_tracker, mock_gemini_client):
    """Verify pipeline deletes only stale questions of targeted topic and type."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    now = datetime.now(UTC)
    q_num = StoredQuestion(
        type="numerical",
        topic_id="t1",
        difficulty=1,
        stem="T1 Numerical",
        options=[],
        answer=QuestionAnswer(value="1"),
        explanation="Exp",
        verification=QuestionVerification(method="python", passed=True, detail="ok"),
        status="verified",
        batch_id=f"t1-num-v{NUMERICAL_PROMPT_VERSION}",
        created_at=now,
    )
    q_t2_mcq = StoredQuestion(
        type="mcq",
        topic_id="t2",
        difficulty=1,
        stem="T2 MCQ",
        options=[QuestionOption(id=c, text=c) for c in "abcd"],
        answer=QuestionAnswer(option_id="a"),
        explanation="Exp",
        verification=QuestionVerification(method="blind_mcq", passed=True, detail="ok"),
        status="verified",
        batch_id=f"t2-mcq-v{MCQ_PROMPT_VERSION}",
        created_at=now,
    )
    q_t1_stale = StoredQuestion(
        type="mcq",
        topic_id="t1",
        difficulty=1,
        stem="T1 Stale MCQ",
        options=[QuestionOption(id=c, text=c) for c in "abcd"],
        answer=QuestionAnswer(option_id="a"),
        explanation="Exp",
        verification=QuestionVerification(method="blind_mcq", passed=True, detail="ok"),
        status="verified",
        batch_id="t1-mcq-old",
        created_at=now,
    )
    put_question(nb_id, f"t1-numerical-v{NUMERICAL_PROMPT_VERSION}-01", q_num)
    put_question(nb_id, f"t2-mcq-v{MCQ_PROMPT_VERSION}-01", q_t2_mcq)
    put_question(nb_id, f"t1-mcq-v{MCQ_PROMPT_VERSION}-99", q_t1_stale)

    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)
    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "What is prob?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq"],
        per_type=1,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert exit_code == 0

    assert get_question(nb_id, f"t1-numerical-v{NUMERICAL_PROMPT_VERSION}-01") is not None
    assert get_question(nb_id, f"t2-mcq-v{MCQ_PROMPT_VERSION}-01") is not None
    assert get_question(nb_id, f"t1-mcq-v{MCQ_PROMPT_VERSION}-99") is None


def test_numerical_prompt_determinism():
    """Verify numerical generation and retry prompt builders produce byte-identical strings."""
    s1, c1 = build_numerical_gen_prompt("t1", "Topic 1", "Summary", "Passages", 5)
    s2, c2 = build_numerical_gen_prompt("t1", "Topic 1", "Summary", "Passages", 5)
    assert s1 == s2
    assert c1 == c2

    failed = [
        {
            "index": 1,
            "stem": "Stem",
            "stated_answer": "1",
            "solution_code": "answer = 1",
            "error_detail": "Detail",
        }
    ]
    rs1, rc1 = build_numerical_retry_prompt("t1", failed, "Passages")
    rs2, rc2 = build_numerical_retry_prompt("t1", failed, "Passages")
    assert rs1 == rs2
    assert rc1 == rc2


# ---------------------------------------------------------------------------
# Task Q1 Live Run Schema & Per-Item Validation Tests (4a, 4b, 4c)
# ---------------------------------------------------------------------------


def extract_schema_keywords(schema_node: Any) -> set[str]:
    """Recursively collect JSON Schema keyword keys from schema dict."""
    keywords = set()
    if isinstance(schema_node, dict):
        for k, v in schema_node.items():
            keywords.add(k)
            if k == "properties" and isinstance(v, dict):
                for _prop_name, prop_def in v.items():
                    keywords.update(extract_schema_keywords(prop_def))
            elif isinstance(v, dict):
                keywords.update(extract_schema_keywords(v))
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        keywords.update(extract_schema_keywords(item))
    return keywords


def test_schema_compatibility_subset_and_no_additional_properties():
    """4a: Batch schemas must be a subset of /ask keywords + enum/nullable, no extras."""
    ask_schema_dict = genai_transformers.t_schema(None, AskGeminiOutput).to_json_dict()
    ask_keywords = extract_schema_keywords(ask_schema_dict)

    batch_models = [
        ("MCQGenerationBatch", MCQGenerationBatch),
        ("BlindMCQBatch", BlindMCQBatch),
        ("NumericalGenerationBatch", NumericalGenerationBatch),
        ("NumericalRetryBatch", NumericalRetryBatch),
    ]

    allowed_keywords = ask_keywords | {"enum"}

    for name, model_cls in batch_models:
        schema_dict = genai_transformers.t_schema(None, model_cls).to_json_dict()
        model_keywords = extract_schema_keywords(schema_dict)
        raw_json = json.dumps(schema_dict)

        # Assert no forbidden additionalProperties keywords exist
        assert "additionalProperties" not in raw_json, f"{name} contains additionalProperties"
        assert "additional_properties" not in raw_json, f"{name} contains additional_properties"
        assert "additionalProperties" not in model_keywords
        assert "additional_properties" not in model_keywords

        # Assert keywords are subset of ask keywords + allowed
        diff = model_keywords - allowed_keywords
        msg = f"{name} produced forbidden schema keywords: {diff}"
        assert model_keywords.issubset(allowed_keywords), msg


def test_per_item_mcq_three_options_rejected_rest_stored(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """4b: Item with 3 options is rejected with 'rejected: ', while valid item verified."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "mcq")

    # Q1 has 3 options (rejected), Q2 has 4 options (valid)
    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Q1 3 options stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(3)],
                "correct_index": 0,
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 1,
                "stem": "Q2 4 options stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })

    raw_opts = [GeneratedMCQOption(text=f"Opt {i}", misconception="") for i in range(4)]
    _, q2_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-02", raw_opts, 0)

    blind_json = json.dumps({
        "answers": [
            {"question_index": 1, "selected_option_id": "a"},
            {"question_index": 2, "selected_option_id": q2_key},
        ]
    })

    def fake_generate(model, contents, config):
        if "blind" in model or "m-fallback" in model:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="Topic 1",
        topic_summary="Summary",
        passages=passages,
        count=2,
        configured_chain=["m-primary", "m-fallback"],
    )

    assert len(stored) == 2
    q1 = stored[0][1]
    q2 = stored[1][1]

    assert q1.status == "rejected"
    assert q1.verification.passed is False
    assert q1.verification.detail.startswith("rejected: ")
    assert "4 options" in q1.verification.detail

    assert q2.status == "verified"
    assert q2.verification.passed is True


def test_per_item_mcq_difficulty_four_rejected_rest_stored(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """4b: Item with difficulty 4 is rejected with 'rejected: ', while valid item verified."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "mcq")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 4,
                "stem": "Q1 difficulty 4 stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 2,
                "stem": "Q2 valid difficulty stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })

    raw_opts = [GeneratedMCQOption(text=f"Opt {i}", misconception="") for i in range(4)]
    _, q2_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-02", raw_opts, 0)

    blind_json = json.dumps({
        "answers": [
            {"question_index": 1, "selected_option_id": "a"},
            {"question_index": 2, "selected_option_id": q2_key},
        ]
    })

    def fake_generate(model, contents, config):
        if "blind" in model or "m-fallback" in model:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="Topic 1",
        topic_summary="Summary",
        passages=passages,
        count=2,
        configured_chain=["m-primary", "m-fallback"],
    )

    assert len(stored) == 2
    q1 = stored[0][1]
    q2 = stored[1][1]

    assert q1.status == "rejected"
    assert q1.verification.passed is False
    assert q1.verification.detail.startswith("rejected: ")
    assert "difficulty" in q1.verification.detail

    assert q2.status == "verified"
    assert q2.verification.passed is True


def test_per_item_mcq_correct_index_five_rejected_rest_stored(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """4b: Item with correct_index 5 is rejected with 'rejected: ', while valid item verified."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "mcq")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Q1 correct_index 5 stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 5,
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 1,
                "stem": "Q2 valid index stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })

    raw_opts = [GeneratedMCQOption(text=f"Opt {i}", misconception="") for i in range(4)]
    _, q2_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-02", raw_opts, 0)

    blind_json = json.dumps({
        "answers": [
            {"question_index": 1, "selected_option_id": "a"},
            {"question_index": 2, "selected_option_id": q2_key},
        ]
    })

    def fake_generate(model, contents, config):
        if "blind" in model or "m-fallback" in model:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="Topic 1",
        topic_summary="Summary",
        passages=passages,
        count=2,
        configured_chain=["m-primary", "m-fallback"],
    )

    assert len(stored) == 2
    q1 = stored[0][1]
    q2 = stored[1][1]

    assert q1.status == "rejected"
    assert q1.verification.passed is False
    assert q1.verification.detail.startswith("rejected: ")
    assert "correct_index" in q1.verification.detail

    assert q2.status == "verified"
    assert q2.verification.passed is True


def test_per_item_mcq_overlong_stem_rejected_rest_stored(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """4b: Item with stem > 800 chars is rejected with 'rejected: ', while valid item verified."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "mcq")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "S" * 801,
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 1,
                "stem": "Q2 normal stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })

    raw_opts = [GeneratedMCQOption(text=f"Opt {i}", misconception="") for i in range(4)]
    _, q2_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-02", raw_opts, 0)

    blind_json = json.dumps({
        "answers": [
            {"question_index": 1, "selected_option_id": "a"},
            {"question_index": 2, "selected_option_id": q2_key},
        ]
    })

    def fake_generate(model, contents, config):
        if "blind" in model or "m-fallback" in model:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="Topic 1",
        topic_summary="Summary",
        passages=passages,
        count=2,
        configured_chain=["m-primary", "m-fallback"],
    )

    assert len(stored) == 2
    q1 = stored[0][1]
    q2 = stored[1][1]

    assert q1.status == "rejected"
    assert q1.verification.passed is False
    assert q1.verification.detail.startswith("rejected: ")
    assert "stem exceeds 800" in q1.verification.detail

    assert q2.status == "verified"
    assert q2.verification.passed is True


def test_per_item_blind_answer_e_rejected_rest_stored(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """4b: Blind check answer 'e' is rejected with 'rejected: ' detail (chose none)."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "mcq")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Q1 valid stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 1,
                "stem": "Q2 valid stem?",
                "options": [{"text": f"Opt {i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })

    raw_opts = [GeneratedMCQOption(text=f"Opt {i}", misconception="") for i in range(4)]
    _, q2_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-02", raw_opts, 0)

    # Q1 gets answer 'e' (invalid option id), Q2 gets matching key
    blind_json = json.dumps({
        "answers": [
            {"question_index": 1, "selected_option_id": "e"},
            {"question_index": 2, "selected_option_id": q2_key},
        ]
    })

    def fake_generate(model, contents, config):
        if "blind" in model or "m-fallback" in model:
            return MagicMock(text=blind_json)
        return MagicMock(text=gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="Topic 1",
        topic_summary="Summary",
        passages=passages,
        count=2,
        configured_chain=["m-primary", "m-fallback"],
    )

    assert len(stored) == 2
    q1 = stored[0][1]
    q2 = stored[1][1]

    assert q1.status == "rejected"
    assert q1.verification.passed is False
    assert q1.verification.detail.startswith("rejected: ")
    assert "chose none" in q1.verification.detail

    assert q2.status == "verified"
    assert q2.verification.passed is True


def test_per_item_numerical_validation_rejected_rest_stored(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """4b: Numerical items with validation errors rejected, valid item verified."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "numerical")

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Q1 empty answer stem",
                "stated_answer": "",
                "solution_code": "answer = 1",
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 4,
                "stem": "Q2 difficulty 4 stem",
                "stated_answer": "10",
                "solution_code": "answer = 10",
                "explanation": "Exp 2",
                "sources": [1],
            },
            {
                "difficulty": 1,
                "stem": "Q3 valid numerical stem",
                "stated_answer": "42",
                "solution_code": "answer = 42",
                "explanation": "Exp 3",
                "sources": [1],
            },
        ]
    })

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(text=gen_json)

    stored, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="Topic 1",
        topic_summary="Summary",
        passages=passages,
        count=3,
        configured_chain=["m-primary"],
    )

    assert len(stored) == 3
    q1 = stored[0][1]
    q2 = stored[1][1]
    q3 = stored[2][1]

    assert q1.status == "rejected"
    assert q1.verification.passed is False
    assert q1.verification.detail.startswith("rejected: ")
    assert "stated_answer" in q1.verification.detail

    assert q2.status == "rejected"
    assert q2.verification.passed is False
    assert q2.verification.detail == "rejected: difficulty must be 1 to 3"

    assert q3.status == "verified"
    assert q3.verification.passed is True
    assert q3.answer.value == "42"


def test_rejected_request_400_stops_run_exit_1(notebook_tracker, mock_gemini_client, tmp_path):
    """4c: SDK 400 in first batch stops run: exit 1, status request_rejected, 1 call."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    mock_gemini_client.models.generate_content.side_effect = errors.ClientError(
        400, "Unknown name additional_properties at generation_config.response_schema"
    )

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1", "t2"],
        types=["mcq", "numerical"],
        per_type=2,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert exit_code == 1
    assert mock_gemini_client.models.generate_content.call_count == 1

    report_path = tmp_path / "derived" / "question_bank" / "report.json"
    assert report_path.exists()
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_data["status"] == "request_rejected"




def test_pipeline_calls_pass_longer_timeouts_and_deadlines(
    notebook_tracker, mock_gemini_client, cache_tracker, monkeypatch
):
    """Assert pipeline generate_json_with_model calls pass timeout=120 and deadline=300."""
    import app.questions.bank as bank_mod

    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    captured_calls: list[dict[str, Any]] = []
    orig_generate = bank_mod.generate_json_with_model

    def spy_generate(*args, **kwargs):
        captured_calls.append(dict(kwargs))
        return orig_generate(*args, **kwargs)

    monkeypatch.setattr(bank_mod, "generate_json_with_model", spy_generate)

    # 1. MCQ batch mocks (generation + blind check)
    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)
    mcq_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "What is prob?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    mcq_blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    # 2. Numerical batch mocks (generation with item needing retry + retry batch)
    num_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 1.",
                "stated_answer": "1",
                "solution_code": "answer = 0  # wrong to force retry",
                "explanation": "fail",
                "sources": [1],
            }
        ]
    })
    num_retry_json = json.dumps({
        "items": [
            {
                "index": 1,
                "stated_answer": "1",
                "solution_code": "answer = 1",
                "explanation": "fixed",
            }
        ]
    })

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=mcq_blind_json)
        if "correcting solution snippets" in sys_inst.lower() or "retrying" in sys_inst.lower():
            return MagicMock(text=num_retry_json)
        if "numerical" in sys_inst.lower() or "numerical" in contents.lower():
            return MagicMock(text=num_gen_json)
        return MagicMock(text=mcq_gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    passages_mcq = select_topic_passages(nb_id, "t1", "mcq")
    passages_num = select_topic_passages(nb_id, "t1", "numerical")

    stored_mcq, rep_mcq = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages_mcq,
        count=1,
        configured_chain=["m-primary", "m-fallback"],
    )

    stored_num, rep_num = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages_num,
        count=1,
        configured_chain=["m-primary"],
    )

    # All 4 pipeline calls: MCQ gen, MCQ blind, Numerical gen, Numerical retry
    assert len(captured_calls) == 4
    for call_kwargs in captured_calls:
        assert call_kwargs.get("attempt_timeout_s") == 120.0
        assert call_kwargs.get("deadline_s") == 300.0



# ---------------------------------------------------------------------------
# Task Q1 Live Run 3 Fix Tests (No-Reuse Rule, Version 2, Model Roles)
# ---------------------------------------------------------------------------


def test_prompts_contain_exact_no_reuse_rule():
    """Verify mcq gen, numerical gen, and numerical retry prompts contain exact no-reuse rule."""
    expected_rule = (
        "Do not reuse a worked example, exercise or recitation problem from the "
        "passages with its setting and its numbers. Writing the same numbers in "
        "another form (0.4 for 2/5, a percentage for a fraction) still counts "
        "as reuse. Keep the idea the passage teaches, but invent a new scenario "
        "and new numbers."
    )
    mcq_sys, _ = build_mcq_gen_prompt("t1", "Topic", "Summary", "Passages")
    assert expected_rule in mcq_sys

    num_sys, _ = build_numerical_gen_prompt("t1", "Topic", "Summary", "Passages")
    assert expected_rule in num_sys

    retry_sys, _ = build_numerical_retry_prompt("t1", [], "Passages")
    assert expected_rule in retry_sys


def test_prompt_versions_are_v2_and_question_ids(
    notebook_tracker, mock_gemini_client, cache_tracker
):
    """Verify both prompt versions are '2' and generated question IDs use 't1-mcq-v2-01'."""
    assert MCQ_PROMPT_VERSION == "2"
    assert NUMERICAL_PROMPT_VERSION == "2"

    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "mcq")

    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)

    mcq_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "What is probability?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=blind_json)
        return MagicMock(text=mcq_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_mcq_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=1,
        configured_chain=["m-primary", "m-fallback"],
    )

    assert stored[0][0] == "t1-mcq-v2-01"
    assert stored[0][1].batch_id == "t1-mcq-v2"


def test_report_json_and_markdown_model_roles(
    notebook_tracker, mock_gemini_client, cache_tracker, tmp_path
):
    """Verify report.json has generator_model and checker_model (mcq has both, num has null)."""
    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(f"t1-mcq-v{MCQ_PROMPT_VERSION}-01", raw_opts, 0)

    mcq_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "What is prob?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    mcq_blind_json = json.dumps({"answers": [{"question_index": 1, "selected_option_id": q1_key}]})

    num_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 1.",
                "stated_answer": "1",
                "solution_code": "answer = 1",
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=mcq_blind_json)
        if "numerical" in sys_inst.lower() or "numerical" in contents.lower():
            return MagicMock(text=num_gen_json)
        return MagicMock(text=mcq_gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=1,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert exit_code == 0

    report_path = tmp_path / "derived" / "question_bank" / "report.json"
    assert report_path.exists()
    report_data = json.loads(report_path.read_text(encoding="utf-8"))

    mcq_batch = next(b for b in report_data["batches"] if b["type"] == "mcq")
    num_batch = next(b for b in report_data["batches"] if b["type"] == "numerical")

    assert mcq_batch["generator_model"] is not None
    assert mcq_batch["checker_model"] is not None
    assert mcq_batch["generator_model"] != mcq_batch["checker_model"]

    assert num_batch["generator_model"] is not None
    assert num_batch["checker_model"] is None

    md_path = tmp_path / "derived" / "question_bank" / "questions.md"
    assert md_path.exists()
    md_text = md_path.read_text(encoding="utf-8")
    assert f"generated by {mcq_batch['generator_model']}" in md_text
    assert f"checked by {mcq_batch['checker_model']}" in md_text
    assert f"generated by {num_batch['generator_model']}" in md_text


# ---------------------------------------------------------------------------
# Task Q1 Quarantine Tests
# ---------------------------------------------------------------------------


def test_quarantine_real_dict_keys_match_pattern_and_prompt_versions():
    """Every real QUARANTINE key matches regex, prompt version, and has no quotes/backslashes."""
    import re

    from app.questions import bank

    assert len(bank.QUARANTINE) > 0
    id_pattern = re.compile(r"^t[1-6]-(mcq|numerical)-v[0-9]+-[0-9]{2}$")

    for qid, reason in bank.QUARANTINE.items():
        assert id_pattern.match(qid), f"Key {qid} does not match expected regex"
        if "-mcq-" in qid:
            assert f"-v{MCQ_PROMPT_VERSION}-" in qid, f"{qid} prompt version mismatch"
        elif "-numerical-" in qid:
            assert f"-v{NUMERICAL_PROMPT_VERSION}-" in qid, f"{qid} prompt version mismatch"
        assert reason, f"Reason for {qid} must not be empty"
        assert '"' not in reason, f"Reason for {qid} contains double quote: {reason}"
        assert "'" not in reason, f"Reason for {qid} contains single quote: {reason}"
        assert "\\" not in reason, f"Reason for {qid} contains backslash: {reason}"


def test_quarantined_questions_stored_rejected_and_excluded_from_verified(
    notebook_tracker, mock_gemini_client, cache_tracker, monkeypatch, tmp_path
):
    """Verified MCQ & numerical in QUARANTINE are stored as rejected and excluded from verified."""
    from app.questions import bank

    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    mcq_q1_id = f"t1-mcq-v{MCQ_PROMPT_VERSION}-01"
    mcq_q2_id = f"t1-mcq-v{MCQ_PROMPT_VERSION}-02"
    num_q1_id = f"t1-numerical-v{NUMERICAL_PROMPT_VERSION}-01"
    num_q2_id = f"t1-numerical-v{NUMERICAL_PROMPT_VERSION}-02"

    test_quarantine = {
        mcq_q1_id: "TEST: mcq quarantine reason",
        num_q1_id: "TEST: numerical quarantine reason",
    }
    monkeypatch.setattr(bank, "QUARANTINE", test_quarantine)

    raw_opts_1 = [GeneratedMCQOption(text=f"Q1_O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(mcq_q1_id, raw_opts_1, 0)
    raw_opts_2 = [GeneratedMCQOption(text=f"Q2_O{i}", misconception="") for i in range(4)]
    _, q2_key = shuffle_mcq_options(mcq_q2_id, raw_opts_2, 1)

    mcq_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "MCQ stem 1?",
                "options": [{"text": f"Q1_O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 2,
                "stem": "MCQ stem 2?",
                "options": [{"text": f"Q2_O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 1,
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })
    mcq_blind_json = json.dumps({
        "answers": [
            {"question_index": 1, "selected_option_id": q1_key},
            {"question_index": 2, "selected_option_id": q2_key},
        ]
    })

    num_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 1/2.",
                "stated_answer": "1/2",
                "solution_code": "answer = Fraction(1, 2)",
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 2,
                "stem": "Compute 3/4.",
                "stated_answer": "3/4",
                "solution_code": "answer = Fraction(3, 4)",
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=mcq_blind_json)
        if "numerical" in sys_inst.lower() or "numerical" in contents.lower():
            return MagicMock(text=num_gen_json)
        return MagicMock(text=mcq_gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq", "numerical"],
        per_type=2,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert exit_code == 0

    # Stored docs verification
    q_mcq_1 = get_question(nb_id, mcq_q1_id)
    assert q_mcq_1 is not None
    assert q_mcq_1.status == "rejected"
    assert q_mcq_1.verification.passed is False
    assert q_mcq_1.verification.detail.startswith("quarantined: ")
    assert "TEST: mcq quarantine reason" in q_mcq_1.verification.detail
    assert "| original: " in q_mcq_1.verification.detail

    q_num_1 = get_question(nb_id, num_q1_id)
    assert q_num_1 is not None
    assert q_num_1.status == "rejected"
    assert q_num_1.verification.passed is False
    assert q_num_1.verification.detail.startswith("quarantined: ")
    assert "TEST: numerical quarantine reason" in q_num_1.verification.detail
    assert "| original: " in q_num_1.verification.detail
    assert q_num_1.solution_code == "answer = Fraction(1, 2)"
    assert q_num_1.answer.value == "1/2"

    # Neighbours stay verified
    q_mcq_2 = get_question(nb_id, mcq_q2_id)
    assert q_mcq_2 is not None
    assert q_mcq_2.status == "verified"
    assert q_mcq_2.verification.passed is True

    q_num_2 = get_question(nb_id, num_q2_id)
    assert q_num_2 is not None
    assert q_num_2.status == "verified"
    assert q_num_2.verification.passed is True

    # list_verified_questions only returns neighbours
    verified_pairs = list_verified_questions(nb_id, "t1")
    verified_qids = {p[0] for p in verified_pairs}
    assert mcq_q1_id not in verified_qids
    assert num_q1_id not in verified_qids
    assert mcq_q2_id in verified_qids
    assert num_q2_id in verified_qids

    # questions_verified excludes quarantined questions (2 out of 4)
    nb_doc = get_notebook_snapshot(nb_id).to_dict()
    assert nb_doc["counts"]["questions_verified"] == 2

    # report.json shows quarantined_applied and quarantine_total
    report_path = tmp_path / "derived" / "question_bank" / "report.json"
    assert report_path.exists()
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_data["quarantined_applied"] == 2
    assert report_data["quarantine_total"] == 2

    # questions.md marks quarantined questions with QUARANTINED: <reason> under heading
    md_path = tmp_path / "derived" / "question_bank" / "questions.md"
    assert md_path.exists()
    md_text = md_path.read_text(encoding="utf-8")
    assert f"### {mcq_q1_id} [REJECTED]" in md_text
    assert "QUARANTINED: TEST: mcq quarantine reason" in md_text
    assert f"### {num_q1_id} [REJECTED]" in md_text
    assert "QUARANTINED: TEST: numerical quarantine reason" in md_text


def test_question_already_rejected_keeps_own_detail_not_counted_in_quarantined(
    notebook_tracker, mock_gemini_client, cache_tracker, monkeypatch, tmp_path
):
    """A question already rejected keeps own rejection detail and not counted as quarantined."""
    from app.questions import bank

    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)

    mcq_q1_id = f"t1-mcq-v{MCQ_PROMPT_VERSION}-01"
    monkeypatch.setattr(bank, "QUARANTINE", {mcq_q1_id: "TEST: should not be applied"})

    raw_opts = [GeneratedMCQOption(text=f"O{i}", misconception="") for i in range(4)]
    _, q1_key = shuffle_mcq_options(mcq_q1_id, raw_opts, 0)
    mismatch_key = "d" if q1_key != "d" else "a"

    mcq_gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Question stem?",
                "options": [{"text": f"O{i}", "misconception": ""} for i in range(4)],
                "correct_index": 0,
                "explanation": "Exp",
                "sources": [1],
            }
        ]
    })
    # Blind checker chooses mismatching option to force rejection before quarantine
    mcq_blind_json = json.dumps({
        "answers": [{"question_index": 1, "selected_option_id": mismatch_key}]
    })

    def fake_generate(model, contents, config):
        sys_inst = getattr(config, "system_instruction", "") or ""
        if "selected_option_id" in sys_inst or "Select the correct option" in contents:
            return MagicMock(text=mcq_blind_json)
        return MagicMock(text=mcq_gen_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    exit_code = bqb_script.build_question_bank(
        notebook_id=nb_id,
        topics=["t1"],
        types=["mcq"],
        per_type=1,
        max_calls=10,
        min_interval_s=0.0,
    )
    assert exit_code == 0

    q_mcq_1 = get_question(nb_id, mcq_q1_id)
    assert q_mcq_1 is not None
    assert q_mcq_1.status == "rejected"
    assert q_mcq_1.verification.passed is False
    assert q_mcq_1.verification.detail.startswith("mismatch: ")
    assert not q_mcq_1.verification.detail.startswith("quarantined: ")

    report_path = tmp_path / "derived" / "question_bank" / "report.json"
    assert report_path.exists()
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_data["quarantined_applied"] == 0
    assert report_data["quarantine_total"] == 1


def test_numerical_quarantine_retry_pass_and_failure(
    notebook_tracker, mock_gemini_client, cache_tracker, monkeypatch
):
    """Numerical retry pass is quarantined if in QUARANTINE; double fail keeps own error detail."""
    import app.questions.bank as bank_mod

    nb_id = f"nb_test_{uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    seed_test_notebook(nb_id)
    passages = select_topic_passages(nb_id, "t1", "numerical")

    num_q1_id = f"t1-numerical-v{NUMERICAL_PROMPT_VERSION}-01"
    num_q2_id = f"t1-numerical-v{NUMERICAL_PROMPT_VERSION}-02"

    monkeypatch.setattr(
        bank_mod,
        "QUARANTINE",
        {
            num_q1_id: "TEST: fixed by retry but quarantined",
            num_q2_id: "TEST: failed twice and should keep fail detail",
        },
    )

    gen_json = json.dumps({
        "questions": [
            {
                "difficulty": 1,
                "stem": "Compute 5/2.",
                "stated_answer": "5/2",
                "solution_code": "answer = 2  # wrong first time",
                "explanation": "Exp 1",
                "sources": [1],
            },
            {
                "difficulty": 1,
                "stem": "Compute 7/2.",
                "stated_answer": "7/2",
                "solution_code": "answer = 1  # wrong first time",
                "explanation": "Exp 2",
                "sources": [1],
            },
        ]
    })
    retry_json = json.dumps({
        "items": [
            {
                "index": 1,
                "stated_answer": "5/2",
                "solution_code": "answer = Fraction(5, 2)",
                "explanation": "Fixed on retry",
            },
            {
                "index": 2,
                "stated_answer": "7/2",
                "solution_code": "answer = 2  # still wrong on retry",
                "explanation": "Still wrong",
            },
        ]
    })

    calls = 0

    def fake_generate(model, contents, config):
        nonlocal calls
        calls += 1
        return MagicMock(text=gen_json if calls == 1 else retry_json)

    mock_gemini_client.models.generate_content.side_effect = fake_generate

    stored, rep = process_numerical_batch(
        nb_id=nb_id,
        topic_id="t1",
        topic_name="T1",
        topic_summary="Summ",
        passages=passages,
        count=2,
        configured_chain=["m-primary"],
    )

    q1 = stored[0][1]
    assert q1.status == "rejected"
    assert q1.verification.passed is False
    assert q1.verification.detail.startswith("quarantined: ")
    assert "TEST: fixed by retry but quarantined" in q1.verification.detail
    assert q1.solution_code == "answer = Fraction(5, 2)"
    assert q1.answer.value == "5/2"

    q2 = stored[1][1]
    assert q2.status == "rejected"
    assert q2.verification.passed is False
    assert not q2.verification.detail.startswith("quarantined: ")
    assert "| retry:" in q2.verification.detail

    assert rep["quarantined_applied"] == 1
    assert rep["verified"] == 0
    assert rep["rejected"] == 2


