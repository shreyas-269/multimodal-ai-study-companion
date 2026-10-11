import csv
import json
import os
import re
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Path to eval testset
TESTSET_PATH = Path(__file__).resolve().parent.parent.parent / "eval" / "testset.json"

# Course data dir
DEFAULT_COURSE_DIR = Path(r"C:\Users\Saksham Chaudhry\course-data\6041")
COURSE_DATA_DIR = Path(os.environ.get("COURSE_DATA_DIR", DEFAULT_COURSE_DIR))


@pytest.fixture(scope="module", autouse=True)
def check_emulator_preconditions():
    """Override session fixture from conftest.py: E1 testset validation is completely offline."""
    yield


class ExpectedSourceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    file_rel: str
    location_type: Literal["video", "slide", "page"]
    page: int | None = None
    page_label: str | None = None
    slide: int | None = None
    t_start_s: float | None = None
    t_end_s: float | None = None
    youtube_id: str | None = None


class MultiTurnScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_description: str
    preceding_user_question: str
    preceding_assistant_context: str
    stateless_endpoint_note: str


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Literal[
        "lecture_example",
        "lecture_slide",
        "textbook",
        "multi_turn_followup",
        "outside_course",
    ]
    source_modality: Literal["video", "slide", "textbook", "chat_scenario", "none"]
    topic_id: str | None = None
    in_course: bool
    question: str = Field(min_length=10, max_length=2000)
    reference_answer: str = Field(min_length=10)
    acceptable_variations: list[str] = Field(default_factory=list)
    key_terms: list[str] = Field(min_length=2)
    expected_sources: list[ExpectedSourceLocation] = Field(default_factory=list)
    multi_turn: MultiTurnScenario | None = None

    @field_validator("id")
    @classmethod
    def validate_id_pattern(cls, v: str) -> str:
        if not re.fullmatch(r"^eval-(lec|slide|book|turn|off)-[0-9]{2}$", v):
            raise ValueError(f"Invalid case ID format: {v}")
        return v

    @field_validator("topic_id")
    @classmethod
    def validate_topic_id(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"^(t[1-6]|other)$", v):
            raise ValueError(f"Invalid topic_id format: {v}")
        return v


def load_testset_raw() -> list[dict]:
    assert TESTSET_PATH.exists(), f"Testset file does not exist at {TESTSET_PATH}"
    with open(TESTSET_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list), "Testset root must be a JSON array"
    return data


def test_testset_schema_and_types():
    """Verify every case in testset.json parses successfully against the strict Pydantic model."""
    raw_cases = load_testset_raw()
    parsed_cases = [EvalCase.model_validate(c) for c in raw_cases]
    assert len(parsed_cases) == 25


def test_testset_case_counts_and_distribution():
    """Verify total count is 25, IDs are unique, and categories match the approved plan."""
    raw_cases = load_testset_raw()
    parsed_cases = [EvalCase.model_validate(c) for c in raw_cases]

    # Exactly 25 cases
    assert len(parsed_cases) == 25

    # Unique IDs
    ids = [c.id for c in parsed_cases]
    assert len(ids) == len(set(ids)), f"Duplicate IDs detected: {ids}"

    # Category counts
    cat_counts: dict[str, int] = {}
    for c in parsed_cases:
        cat_counts[c.category] = cat_counts.get(c.category, 0) + 1

    assert cat_counts.get("lecture_example") == 6
    assert cat_counts.get("lecture_slide") == 6
    assert cat_counts.get("textbook") == 7
    assert cat_counts.get("multi_turn_followup") == 3
    assert cat_counts.get("outside_course") == 3

    # Modality counts
    mod_counts: dict[str, int] = {}
    for c in parsed_cases:
        mod_counts[c.source_modality] = mod_counts.get(c.source_modality, 0) + 1

    assert mod_counts.get("video") == 6
    assert mod_counts.get("slide") == 6
    assert mod_counts.get("textbook") == 7
    assert mod_counts.get("chat_scenario") == 3
    assert mod_counts.get("none") == 3

    # Topic coverage: topics t1 through t6 all present
    topics = {c.topic_id for c in parsed_cases if c.topic_id is not None}
    for t_idx in range(1, 7):
        assert f"t{t_idx}" in topics

    # Outside course constraints
    off_course = [c for c in parsed_cases if c.category == "outside_course"]
    for c in off_course:
        assert c.in_course is False
        assert c.topic_id is None
        assert c.source_modality == "none"
        assert len(c.expected_sources) == 0
        assert c.multi_turn is None

    # Multi-turn scenarios constraints
    multi_turn = [c for c in parsed_cases if c.category == "multi_turn_followup"]
    for c in multi_turn:
        assert c.multi_turn is not None
        assert len(c.multi_turn.preceding_user_question) > 5
        assert len(c.multi_turn.preceding_assistant_context) > 5
        assert "stateless" in c.multi_turn.stateless_endpoint_note.lower()


def test_testset_source_locations_against_manifest():
    """Cross-reference all expected sources and locations against local manifest and files."""
    if not COURSE_DATA_DIR.exists():
        pytest.skip(f"Course data directory not found at {COURSE_DATA_DIR}")

    manifest_path = COURSE_DATA_DIR / "manifest.csv"
    assert manifest_path.exists(), f"manifest.csv missing at {manifest_path}"

    with open(manifest_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        manifest_rows = list(reader)

    # Clean stem helper
    def derive_sid(f_rel: str) -> str:
        stem = Path(f_rel).stem.lower()
        clean = re.sub(r"[^a-z0-9]", "_", stem)
        return f"src_{clean}"

    manifest_source_ids = {derive_sid(r["file"]): r for r in manifest_rows}

    raw_cases = load_testset_raw()
    parsed_cases = [EvalCase.model_validate(c) for c in raw_cases]

    for c in parsed_cases:
        if not c.in_course:
            continue

        assert len(c.expected_sources) > 0, f"In-course case {c.id} has no expected sources"

        for s in c.expected_sources:
            # Source ID must be known in manifest
            assert s.source_id in manifest_source_ids, (
                f"Case {c.id} references unknown source_id '{s.source_id}'"
            )
            meta = manifest_source_ids[s.source_id]
            assert s.file_rel == meta["file"]

            # Physical file must exist on disk
            real_file = COURSE_DATA_DIR / s.file_rel
            assert real_file.exists(), f"Course file {real_file} does not exist"

            if s.location_type == "video":
                assert meta["type"] == "video"
                assert s.youtube_id == meta["youtube_id"]
                assert s.t_start_s is not None and s.t_start_s >= 0.0
                assert s.t_end_s is not None and s.t_end_s > s.t_start_s

                # Check transcript exists and duration bounds
                trans_file = (
                    COURSE_DATA_DIR
                    / "derived"
                    / "transcripts"
                    / f"{Path(s.file_rel).stem}.json"
                )
                if trans_file.exists():
                    with open(trans_file, encoding="utf-8") as tf:
                        t_data = json.load(tf)
                    dur = float(t_data.get("duration_s", 0.0))
                    assert s.t_start_s <= dur, (
                        f"Video timestamp {s.t_start_s} exceeds duration {dur}"
                    )

            elif s.location_type == "slide":
                assert meta["type"] == "pdf"
                assert s.slide is not None and s.slide >= 1
                # L01 has 12 slides, L02-L06 have 8 slides
                if "l01" in s.source_id:
                    assert s.slide <= 12
                else:
                    assert s.slide <= 8
                assert s.page is not None and 1 <= s.page <= 3

            elif s.location_type == "page":
                assert meta["type"] == "pdf"
                assert s.page is not None
                if s.source_id == "src_textbook":
                    # Textbook extract has 165 pages; page 1 is license
                    assert 2 <= s.page <= 165
                    assert s.page_label is not None


def test_testset_in_course_key_terms_grounding():
    """Verify that every in-course test case has required key terms grounded in raw source text."""
    if not COURSE_DATA_DIR.exists():
        pytest.skip(f"Course data directory not found at {COURSE_DATA_DIR}")

    raw_cases = load_testset_raw()
    parsed_cases = [EvalCase.model_validate(c) for c in raw_cases]

    import pymupdf

    in_course_count = 0
    total_key_terms = 0
    matched_key_terms = 0

    for c in parsed_cases:
        if not c.in_course:
            continue

        in_course_count += 1
        collected_text = ""
        for s in c.expected_sources:
            if s.location_type == "video":
                trans_path = (
                    COURSE_DATA_DIR
                    / "derived"
                    / "transcripts"
                    / f"{Path(s.file_rel).stem}.json"
                )
                if trans_path.exists():
                    with open(trans_path, encoding="utf-8") as tf:
                        t_data = json.load(tf)
                    collected_text += " " + " ".join(
                        seg["text"] for seg in t_data.get("segments", [])
                    )
            elif s.location_type in ("slide", "page"):
                pdf_path = COURSE_DATA_DIR / s.file_rel
                if pdf_path.exists():
                    with pymupdf.open(pdf_path) as doc:
                        if s.page is not None and 1 <= s.page <= len(doc):
                            p_min = max(0, s.page - 2)
                            p_max = min(len(doc), s.page + 1)
                            for p_idx in range(p_min, p_max):
                                collected_text += " " + doc[p_idx].get_text()
                        else:
                            for page in doc:
                                collected_text += " " + page.get_text()

        collected_lower = collected_text.lower()
        matches = [kw for kw in c.key_terms if kw.lower() in collected_lower]
        missing = [kw for kw in c.key_terms if kw.lower() not in collected_lower]

        total_key_terms += len(c.key_terms)
        matched_key_terms += len(matches)

        # Enforce §7.2.5: all specified key_terms must actually appear in raw source files
        assert not missing, (
            f"Case {c.id} key terms not grounded in source text. "
            f"Expected {c.key_terms}, missing {missing}"
        )

        # Enforce §6.2 grounding threshold per case (100% >= 75%)
        coverage_pct = (len(matches) / len(c.key_terms)) * 100.0
        assert coverage_pct >= 75.0, (
            f"Case {c.id} key term coverage {coverage_pct:.1f}% below 75% threshold"
        )

    assert in_course_count == 22, f"Expected 22 in-course cases, got {in_course_count}"
    aggregate_coverage_pct = (matched_key_terms / total_key_terms) * 100.0
    assert aggregate_coverage_pct >= 75.0, (
        f"Aggregate key term coverage {aggregate_coverage_pct:.1f}% below 75% threshold"
    )
