"""Tests for offline cache pre-warming script.

Guarantees:
- Zero live Gemini quota in tests (mock_gemini_client fixture from conftest).
- Comprehensive validation of CLI args, notebook loading, filtering, dry-run,
  and live warming workflows.
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.db import get_db, llm_cache_path
from app.db.notebooks import DEMO_NOTEBOOK_ID
from scripts.prewarm_cache import (
    check_cache_exists,
    compute_target_cache_keys,
    load_and_filter_testset,
    load_demo_notebook,
    main,
    parse_args,
)


def test_parse_args_defaults():
    """Verify default CLI arguments."""
    args = parse_args([])
    assert args.dry_run is False
    assert args.limit == 25
    assert args.topic is None
    assert args.delay == 12.0
    assert args.notebook == DEMO_NOTEBOOK_ID
    assert args.testset is None


def test_parse_args_overrides():
    """Verify CLI flag overrides."""
    args = parse_args(
        [
            "--dry-run",
            "--limit",
            "5",
            "--topic",
            "t2",
            "--delay",
            "3.5",
            "--notebook",
            "custom_nb",
            "--testset",
            "dummy_testset.json",
        ]
    )
    assert args.dry_run is True
    assert args.limit == 5
    assert args.topic == "t2"
    assert args.delay == 3.5
    assert args.notebook == "custom_nb"
    assert args.testset == "dummy_testset.json"


def test_parse_args_invalid_topic():
    """Verify parser rejects invalid topic."""
    with pytest.raises(SystemExit):
        parse_args(["--topic", "invalid_topic"])


def test_load_demo_notebook_success():
    """Verify demo notebook loads successfully when present in Firestore."""
    nb = load_demo_notebook(DEMO_NOTEBOOK_ID)
    assert nb.id == DEMO_NOTEBOOK_ID
    assert nb.name is not None
    assert len(nb.sources_summary) > 0


def test_load_demo_notebook_missing_raises():
    """Verify load_demo_notebook raises RuntimeError if notebook document is missing."""
    with pytest.raises(RuntimeError, match="not found in Firestore"):
        load_demo_notebook("non_existent_notebook_999")


def test_load_and_filter_testset(tmp_path):
    """Verify testset filtering by topic and limit."""
    sample_cases = [
        {"id": "c1", "topic_id": "t1", "question": "Q1"},
        {"id": "c2", "topic_id": "t1", "question": "Q2"},
        {"id": "c3", "topic_id": "t2", "question": "Q3"},
        {"id": "c4", "topic_id": None, "question": "Q4"},
        {"id": "c5", "topic_id": "other", "question": "Q5"},
    ]
    testset_file = tmp_path / "testset.json"
    testset_file.write_text(json.dumps(sample_cases), encoding="utf-8")

    # All with limit
    cases = load_and_filter_testset(testset_file, topic=None, limit=3)
    assert len(cases) == 3
    assert [c["id"] for c in cases] == ["c1", "c2", "c3"]

    # Filter by topic t1
    cases_t1 = load_and_filter_testset(testset_file, topic="t1", limit=10)
    assert len(cases_t1) == 2
    assert [c["id"] for c in cases_t1] == ["c1", "c2"]

    # Filter by topic other (captures both None and 'other')
    cases_other = load_and_filter_testset(testset_file, topic="other", limit=10)
    assert len(cases_other) == 2
    assert [c["id"] for c in cases_other] == ["c4", "c5"]

    # Missing file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        load_and_filter_testset(tmp_path / "missing.json")


def test_compute_target_cache_keys():
    """Verify compute_target_cache_keys returns valid sha256 hex keys and chunks."""
    nb = load_demo_notebook(DEMO_NOTEBOOK_ID)
    models = ["gemini-3.8-flash", "gemini-3.5-flash"]
    keys, chunks = compute_target_cache_keys(
        notebook=nb,
        question="What is Bayes rule?",
        models=models,
        allow_outside=False,
    )
    assert len(keys) == 2
    assert keys[0][0] == "gemini-3.8-flash"
    assert keys[1][0] == "gemini-3.5-flash"
    assert len(keys[0][1]) == 64  # sha256 hex
    assert len(keys[1][1]) == 64
    assert len(chunks) > 0


def test_check_cache_exists():
    """Verify check_cache_exists detects Firestore and disk cache entries."""
    db = get_db()
    fake_key = f"fake_key_{uuid4().hex}"
    keys = [("gemini-3.8-flash", fake_key)]

    # 1. Miss initially
    is_hit, hit_model, hit_key = check_cache_exists(db, keys)
    assert is_hit is False
    assert hit_model is None
    assert hit_key is None

    # 2. Hit via disk cache dict
    disk_cache = {fake_key: {"model": "gemini-3.8-flash"}}
    is_hit, hit_model, hit_key = check_cache_exists(db, keys, disk_cache=disk_cache)
    assert is_hit is True
    assert hit_model == "gemini-3.8-flash"
    assert hit_key == fake_key


def test_dry_run_zero_llm_calls(mock_gemini_client, capsys):
    """Verify --dry-run completes with status 0 and makes zero LLM calls."""
    ret = main(["--dry-run", "--limit", "3"])
    assert ret == 0

    # Guarantee zero Gemini calls
    assert mock_gemini_client.models.generate_content.call_count == 0

    captured = capsys.readouterr()
    assert "DRY RUN: Cache Pre-Warming Evaluation" in captured.out
    assert "DRY RUN SUMMARY" in captured.out
    assert "Dry run completed successfully with status 0" in captured.out


def test_dry_run_topic_filtering(mock_gemini_client, capsys):
    """Verify --dry-run respects topic filters."""
    ret = main(["--dry-run", "--topic", "other"])
    assert ret == 0
    assert mock_gemini_client.models.generate_content.call_count == 0

    captured = capsys.readouterr()
    assert "Total Evaluated: 3" in captured.out
    assert "eval-off-01" in captured.out
    assert "eval-off-02" in captured.out
    assert "eval-off-03" in captured.out


def test_live_prewarm_existing_cache_hits(mock_gemini_client, capsys):
    """Verify live run skips live calls when questions are already warm in cache."""
    # Since demo testset questions are already warm in emulator cache,
    # running on the first 2 questions should hit cache and make 0 LLM calls.
    ret = main(["--limit", "2"])
    assert ret == 0
    assert mock_gemini_client.models.generate_content.call_count == 0

    captured = capsys.readouterr()
    assert "[CACHE HIT]" in captured.out
    assert "Existing Hits:   2" in captured.out
    assert "Newly Warmed:    0" in captured.out


def test_live_prewarm_cold_cache_miss_and_pacing(
    tmp_path,
    cache_tracker,
    mock_gemini_client,
    capsys,
):
    """Verify cold cache miss triggers run_answer_pipeline, writes cache, and paces delay."""
    # Create an artificial cold question
    cold_question = f"Cold test question {uuid4().hex}?"
    custom_cases = [
        {"id": "cold-01", "topic_id": "t1", "question": cold_question},
        {"id": "cold-02", "topic_id": "t1", "question": f"Another cold question {uuid4().hex}?"},
    ]
    testset_file = tmp_path / "custom_testset.json"
    testset_file.write_text(json.dumps(custom_cases), encoding="utf-8")

    # Mock gemini client to return valid answer output
    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Warmed test answer.", "sources": [1]}]})
    )

    with patch("time.sleep") as mock_sleep:
        ret = main(
            [
                "--testset",
                str(testset_file),
                "--limit",
                "2",
                "--delay",
                "15.0",
            ]
        )
        assert ret == 0

        # Live calls should have been made for both cold questions
        assert mock_gemini_client.models.generate_content.call_count == 2

        # Pacing sleep was called between question 1 and question 2
        mock_sleep.assert_called_once_with(15.0)

    captured = capsys.readouterr()
    assert "[CACHE MISS] cold-01" in captured.out
    assert "[CACHE MISS] cold-02" in captured.out
    assert "Newly Warmed:    2" in captured.out

    # Verify second execution of cold-01 is now a CACHE HIT
    mock_gemini_client.models.generate_content.reset_mock()
    single_case_file = tmp_path / "single_testset.json"
    single_case_file.write_text(json.dumps([custom_cases[0]]), encoding="utf-8")

    ret2 = main(["--testset", str(single_case_file), "--limit", "1"])
    assert ret2 == 0
    # Zero calls to Gemini on second run (cache hit)
    assert mock_gemini_client.models.generate_content.call_count == 0


def test_cache_key_byte_for_byte_parity(tmp_path, cache_tracker, mock_gemini_client):
    """Verify offline cache key matches the doc written by run_answer_pipeline byte-for-byte."""
    from scripts.prewarm_cache import compute_target_cache_keys, load_demo_notebook

    nb = load_demo_notebook(DEMO_NOTEBOOK_ID)
    test_q = f"Parity verification question {uuid4().hex}?"

    testset_file = tmp_path / "parity_testset.json"
    testset_file.write_text(
        json.dumps([{"id": "parity-01", "topic_id": "t1", "question": test_q}]),
        encoding="utf-8",
    )

    mock_gemini_client.models.generate_content.side_effect = None
    mock_gemini_client.models.generate_content.return_value = MagicMock(
        text=json.dumps({"paragraphs": [{"text": "Parity test answer.", "sources": [1]}]})
    )

    # 1. Compute target cache key offline
    keys, _ = compute_target_cache_keys(
        notebook=nb,
        question=test_q,
        models=["gemini-3.8-flash"],
        allow_outside=False,
    )
    expected_cache_key = keys[0][1]

    # 2. Run prewarm script
    ret = main(["--testset", str(testset_file), "--limit", "1"])
    assert ret == 0

    # 3. Verify exact key exists in Firestore llm_cache
    db = get_db()
    cache_doc = db.document(llm_cache_path(expected_cache_key)).get()
    assert cache_doc.exists, f"Expected cache document {expected_cache_key} not found in Firestore!"
    doc_data = cache_doc.to_dict() or {}
    assert doc_data.get("model") == "gemini-3.8-flash"
    assert doc_data.get("prompt_version") == "ask-v2"
