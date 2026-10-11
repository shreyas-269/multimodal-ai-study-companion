import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.chat.citations import build_citation
from app.db.notebooks import get_notebook_snapshot
from app.db.sources import get_sources_metadata_by_ids
from app.llm.exceptions import ModelUnavailable, UnreadableOutput
from app.llm.generate import generate_json_with_model
from app.llm.prompts.questions import (
    MCQ_PROMPT_VERSION,
    NUMERICAL_PROMPT_VERSION,
    BlindMCQBatch,
    GeneratedMCQItem,
    GeneratedMCQOption,
    GeneratedNumericalItem,
    MCQGenerationBatch,
    NumericalGenerationBatch,
    NumericalRetryBatch,
    build_blind_mcq_prompt,
    build_mcq_gen_prompt,
    build_numerical_gen_prompt,
    build_numerical_retry_prompt,
)
from app.models.citation import Citation, Location
from app.models.question import (
    QuestionAnswer,
    QuestionOption,
    QuestionVerification,
    StoredQuestion,
)
from app.questions.numerical import parse_exact, verify_numerical
from app.questions.passages import SelectedPassage, format_passages_block

logger = logging.getLogger(__name__)


# Quarantined after the whole-bank check of 11 Oct 2026. IDs are stable only while the cached model
# responses exist (prompt version 2). If the cache is lost, regenerating changes which question
# gets which ID: delete this dict and re-check the bank.
QUARANTINE: dict[str, str] = {
    "t4-numerical-v2-05": "AMBIGUOUS: 144 if rooks differ, 72 if identical; stem is silent",
    "t6-mcq-v2-07": "BAD MISCONCEPTION: rationales for options c and d describe other values",
    "t6-numerical-v2-08": (
        "LEAKED REASONING: explanation shows generator self-talk (Wait, solution code)"
    ),
    "t5-numerical-v2-07": "LEAKED REASONING: explanation shows wait ... let us recompute",
    "t2-numerical-v2-06": "LEAKED REASONING: explanation ends with a stray question mark and Wait",
    "t1-mcq-v2-04": "REUSED: Rec 1 Q1 states this exact formula; the MCQ just asks for it",
    "t1-mcq-v2-07": "REUSED: textbook p.21 election example restated, plus a D option",
    "t2-mcq-v2-07": "REUSED: 14-token copy of the Rec 2 solutions generalisation",
    "t2-numerical-v2-04": "REUSED: Rec 2 Q1 coin example, tails for heads, same 1/3",
    "t3-mcq-v2-05": (
        "REUSED: Rec 3 Q4(a), same question and answer; duplicate of t3-numerical-v2-01"
    ),
    "t3-mcq-v2-06": "REUSED: Rec 3 Q4(b), same statement",
    "t3-numerical-v2-01": "REUSED: Rec 3 Q4(a) again; code only restates the answer",
    "t3-numerical-v2-03": "REUSED: Rec 3 Q2(a) with p=1/3 plugged in (borderline)",
    "t3-numerical-v2-04": "REUSED: Rec 3 Q2(c), same answer 2/3; code only restates the answer",
    "t3-numerical-v2-06": "REUSED: Lecture 3 slide 2 P(1 head) with p=3/4 plugged in (borderline)",
    "t4-mcq-v2-05": "REUSED: Rec 4 Q4 asks students to derive this formula",
    "t4-mcq-v2-06": "REUSED: Rec 4 Q1 birthday problem; option b shows raw LaTeX",
    "t4-mcq-v2-08": (
        "REUSED: Rec 4 Q3 hypergeometric, numbers plugged in; same urn as t6-numerical-v2-03"
    ),
    "t6-numerical-v2-01": (
        "REUSED: Rec 6 Q1 solution table cell is the same 3/8; die labels shifted"
    ),
    "t2-mcq-v2-05": "BAD MISCONCEPTION: option d (1/5) rationale would give 1/4, not 1/5",
    "t5-mcq-v2-03": "BAD MISCONCEPTION: option c rationale is empty filler text",
    "t5-mcq-v2-05": (
        "WEAK CITATION: option b rationale explains nothing; cite is a derivation, not E[X]"
    ),
    "t5-mcq-v2-06": "BAD MISCONCEPTION: option a (36) rationale describes a different expression",
    "t6-mcq-v2-01": (
        "BAD MISCONCEPTION: option c (7) rationale subtracts beta squared, which does not give 7"
    ),
    "t6-mcq-v2-06": "BAD MISCONCEPTION: option b rationale is vague",
    "t2-mcq-v2-06": (
        "DUPLICATE: same sample-space fact as t1-mcq-v2-01; keep t1 to protect minimums"
    ),
    "t6-numerical-v2-02": "DUPLICATE: same P(T>3)=8/27 problem as t6-mcq-v2-04; keep the MCQ",
}


class BudgetExhausted(Exception):
    """Raised when the next Gemini attempt would exceed --max-calls."""

    pass


def shuffle_mcq_options(
    qid: str,
    raw_options: list[GeneratedMCQOption],
    correct_index: int,
) -> tuple[list[QuestionOption], str]:
    """Deterministically shuffle 4 options using sha256(qid|i) and return (options, key)."""
    order = sorted(
        range(4),
        key=lambda i: hashlib.sha256(f"{qid}|{i}".encode()).hexdigest(),
    )
    shuffled_options = [
        QuestionOption(
            id="abcd"[p],
            text=raw_options[order[p]].text,
            misconception=raw_options[order[p]].misconception or None,
        )
        for p in range(4)
    ]
    key = "abcd"[order.index(correct_index)]
    return shuffled_options, key


def validate_mcq_item(
    item: GeneratedMCQItem,
    max_source_num: int,
    seen_stems: set[str],
) -> tuple[bool, str]:
    """Validate MCQ per-item rules (difficulty, stem, options, length, citations)."""
    if not (1 <= item.difficulty <= 3):
        return False, "difficulty must be 1 to 3"

    if not item.stem or len(item.stem) > 800:
        return False, f"stem exceeds 800 chars ({len(item.stem)})"

    norm_stem = item.stem.strip().lower()
    if norm_stem in seen_stems:
        return False, "duplicate stem within topic"

    if len(item.options) != 4:
        return False, f"expected 4 options, got {len(item.options)}"

    normalized_texts = {opt.text.strip().lower() for opt in item.options}
    if len(normalized_texts) != 4:
        return False, "duplicate option texts detected"

    if not (0 <= item.correct_index <= 3):
        return False, f"correct_index out of range 0-3 ({item.correct_index})"

    for opt in item.options:
        if len(opt.text) > 300:
            return False, f"option text exceeds 300 chars ({len(opt.text)})"
        if opt.misconception and len(opt.misconception) > 300:
            return False, f"misconception exceeds 300 chars ({len(opt.misconception)})"

    if len(item.explanation) > 1000:
        return False, f"explanation exceeds 1000 chars ({len(item.explanation)})"

    valid_sources = [s for s in item.sources if 1 <= s <= max_source_num]
    if not valid_sources:
        return False, "no valid citations: no valid source numbers"

    return True, "ok"


def validate_numerical_item(
    item: GeneratedNumericalItem,
    max_source_num: int,
    seen_stems: set[str],
) -> tuple[bool, str]:
    """Validate numerical per-item rules (difficulty, stem, answer/code, citations)."""
    if not (1 <= item.difficulty <= 3):
        return False, "difficulty must be 1 to 3"

    if not item.stem or len(item.stem) > 800:
        return False, f"stem exceeds 800 chars ({len(item.stem)})"

    norm_stem = item.stem.strip().lower()
    if norm_stem in seen_stems:
        return False, "duplicate stem within topic"

    if len(item.explanation) > 1000:
        return False, f"explanation exceeds 1000 chars ({len(item.explanation)})"

    if not item.stated_answer or not item.stated_answer.strip():
        return False, "stated_answer must be non-empty"

    if not item.solution_code or not item.solution_code.strip():
        return False, "solution_code must be non-empty"

    valid_sources = [s for s in item.sources if 1 <= s <= max_source_num]
    if not valid_sources:
        return False, "no valid citations: no valid source numbers"

    return True, "ok"


def resolve_question_citations(
    cited_nums: list[int],
    passages: list[SelectedPassage],
    sources_summary: list[dict[str, Any]],
    video_meta: dict[str, Any],
) -> list[Citation]:
    """Resolve source numbers to Citation objects matching the Sources endpoint."""
    ready_titles = {
        s.get("source_id"): s.get("title", s.get("source_id"))
        for s in sources_summary
        if s.get("status") == "ready"
    }

    passages_map = {p.passage_num: p for p in passages}
    citations: list[Citation] = []

    for num in cited_nums:
        if num not in passages_map:
            continue
        p = passages_map[num]
        sid = p.source_id
        if sid not in ready_titles:
            continue
        title = ready_titles[sid]

        loc = Location.model_validate(p.loc)
        if loc.t_start_s is not None:
            meta = video_meta.get(sid, {})
            yt_id = meta.get("youtube_id")
            offset_s = meta.get("offset_s")
            cit = build_citation(
                chunk_id=p.chunk_id,
                loc=loc,
                title=title,
                youtube_id=yt_id,
                offset_s=offset_s,
            )
        else:
            cit = build_citation(chunk_id=p.chunk_id, loc=loc, title=title)

        if cit is not None:
            citations.append(cit)

    return citations


def process_mcq_batch(
    nb_id: str,
    topic_id: str,
    topic_name: str,
    topic_summary: str,
    passages: list[SelectedPassage],
    count: int,
    configured_chain: list[str],
    on_attempt: Callable[[], None] | None = None,
) -> tuple[list[tuple[str, StoredQuestion]], dict[str, Any]]:
    """Generate and verify an MCQ batch, returning questions and batch report."""
    passages_str = format_passages_block(passages)
    sys_inst, contents = build_mcq_gen_prompt(
        topic_id=topic_id,
        topic_name=topic_name,
        topic_summary=topic_summary,
        passages_str=passages_str,
        count=count,
    )

    # 1. Generation call
    gen_output, gen_model = generate_json_with_model(
        prompt_version=MCQ_PROMPT_VERSION,
        system_instruction=sys_inst,
        contents=contents,
        schema=MCQGenerationBatch,
        cache_key_parts=[nb_id, topic_id, "mcq-gen"],
        models=configured_chain,
        on_attempt=on_attempt,
        max_output_tokens=8192,
        attempt_timeout_s=120.0,
        deadline_s=300.0,
    )

    nb_snap = get_notebook_snapshot(nb_id)
    nb_data = nb_snap.to_dict() or {} if nb_snap.exists else {}
    sources_summary = nb_data.get("sources_summary", [])
    video_source_ids = sorted(
        {p.source_id for p in passages if p.loc.get("t_start_s") is not None}
    )
    video_meta = (
        get_sources_metadata_by_ids(nb_id, video_source_ids)
        if video_source_ids
        else {}
    )

    batch_id = f"{topic_id}-mcq-v{MCQ_PROMPT_VERSION}"
    now = datetime.now(UTC)

    # Pre-process items: validation, shuffle, citations
    seen_stems: set[str] = set()
    prepared_items: list[dict[str, Any]] = []

    for idx, item in enumerate(gen_output.questions, start=1):
        qid = f"{topic_id}-mcq-v{MCQ_PROMPT_VERSION}-{idx:02d}"
        is_valid, reason = validate_mcq_item(item, len(passages), seen_stems)
        if is_valid:
            seen_stems.add(item.stem.strip().lower())
            shuffled_options, key = shuffle_mcq_options(qid, item.options, item.correct_index)
        else:
            shuffled_options = [
                QuestionOption(id="abcd"[p], text=opt.text, misconception=opt.misconception or None)
                for p, opt in enumerate(item.options[:4])
            ]
            while len(shuffled_options) < 2:
                shuffled_options.append(QuestionOption(id="abcd"[len(shuffled_options)], text=""))
            key = shuffled_options[0].id

        citations = resolve_question_citations(
            item.sources, passages, sources_summary, video_meta
        )

        prepared_items.append({
            "idx": idx,
            "qid": qid,
            "item": item,
            "is_valid": is_valid,
            "reject_reason": reason if not is_valid else None,
            "shuffled_options": shuffled_options,
            "key": key,
            "citations": citations,
        })

    # 2. Blind check call
    blind_chain = [m for m in configured_chain if m != gen_model]
    blind_answers_map: dict[int, str] = {}
    blind_model = "none"

    if not blind_chain:
        # No second model available for blind check
        for prep in prepared_items:
            prep["is_valid"] = False
            prep["reject_reason"] = "rejected: no second model for the blind check"
    else:
        # Run blind check on all items
        blind_q_list = [
            {
                "question_index": prep["idx"],
                "stem": prep["item"].stem,
                "options": [
                    {"id": opt.id, "text": opt.text}
                    for opt in prep["shuffled_options"]
                ],
            }
            for prep in prepared_items
        ]
        blind_sys, blind_contents = build_blind_mcq_prompt(blind_q_list, passages_str)
        blind_output, blind_model = generate_json_with_model(
            prompt_version=MCQ_PROMPT_VERSION,
            system_instruction=blind_sys,
            contents=blind_contents,
            schema=BlindMCQBatch,
            cache_key_parts=[nb_id, topic_id, "mcq-blind"],
            models=blind_chain,
            on_attempt=on_attempt,
            max_output_tokens=8192,
            attempt_timeout_s=120.0,
            deadline_s=300.0,
        )
        blind_answers_map = {
            ans.question_index: ans.selected_option_id for ans in blind_output.answers
        }

    # 3. Assemble StoredQuestions
    stored_pairs: list[tuple[str, StoredQuestion]] = []
    verified_count = 0
    rejected_count = 0
    quarantined_count = 0
    rejected_details: list[str] = []

    for prep in prepared_items:
        qid = prep["qid"]
        item: GeneratedMCQItem = prep["item"]
        shuffled_opts = prep["shuffled_options"]
        key = prep["key"]
        citations = prep["citations"]

        if not prep["is_valid"]:
            status = "rejected"
            passed = False
            detail = f"rejected: {prep['reject_reason']}"
        elif not citations:
            status = "rejected"
            passed = False
            detail = "rejected: no valid citations"
        else:
            blind_choice = blind_answers_map.get(prep["idx"])
            if blind_choice is None or blind_choice not in ("a", "b", "c", "d"):
                status = "rejected"
                passed = False
                detail = f"rejected: mismatch (chose none, key {key})"
            elif blind_choice == key:
                status = "verified"
                passed = True
                detail = f"ok: {blind_model} chose {blind_choice}"
            else:
                status = "rejected"
                passed = False
                detail = f"mismatch: {blind_model} chose {blind_choice}, key {key}"

        if status == "verified":
            if qid in QUARANTINE:
                status = "rejected"
                passed = False
                detail = f"quarantined: {QUARANTINE[qid]} | original: {detail}"
                quarantined_count += 1
                rejected_count += 1
                rejected_details.append(f"{qid}: {detail}")
            else:
                verified_count += 1
        else:
            rejected_count += 1
            rejected_details.append(f"{qid}: {detail}")

        q_doc = StoredQuestion(
            type="mcq",
            topic_id=topic_id,
            difficulty=max(1, min(3, item.difficulty)),
            stem=item.stem,
            options=shuffled_opts,
            answer=QuestionAnswer(option_id=key),
            explanation=item.explanation,
            citations=citations,
            verification=QuestionVerification(
                method="blind_mcq",
                passed=passed,
                detail=detail,
            ),
            status=status,
            batch_id=batch_id,
            created_at=now,
        )
        stored_pairs.append((qid, q_doc))

    report = {
        "topic_id": topic_id,
        "type": "mcq",
        "generated": len(stored_pairs),
        "verified": verified_count,
        "rejected": rejected_count,
        "quarantined_applied": quarantined_count,
        "rejection_details": rejected_details,
        "generator_model": gen_model,
        "checker_model": blind_model,
        "models_used": [gen_model, blind_model],
    }
    return stored_pairs, report


def process_numerical_batch(
    nb_id: str,
    topic_id: str,
    topic_name: str,
    topic_summary: str,
    passages: list[SelectedPassage],
    count: int,
    configured_chain: list[str],
    on_attempt: Callable[[], None] | None = None,
) -> tuple[list[tuple[str, StoredQuestion]], dict[str, Any]]:
    """Generate and verify a numerical batch with single retry call on failure."""
    passages_str = format_passages_block(passages)
    sys_inst, contents = build_numerical_gen_prompt(
        topic_id=topic_id,
        topic_name=topic_name,
        topic_summary=topic_summary,
        passages_str=passages_str,
        count=count,
    )

    # 1. Generation call
    gen_output, gen_model = generate_json_with_model(
        prompt_version=NUMERICAL_PROMPT_VERSION,
        system_instruction=sys_inst,
        contents=contents,
        schema=NumericalGenerationBatch,
        cache_key_parts=[nb_id, topic_id, "num-gen"],
        models=configured_chain,
        on_attempt=on_attempt,
        max_output_tokens=8192,
        attempt_timeout_s=120.0,
        deadline_s=300.0,
    )

    nb_snap = get_notebook_snapshot(nb_id)
    nb_data = nb_snap.to_dict() or {} if nb_snap.exists else {}
    sources_summary = nb_data.get("sources_summary", [])
    video_source_ids = sorted(
        {p.source_id for p in passages if p.loc.get("t_start_s") is not None}
    )
    video_meta = (
        get_sources_metadata_by_ids(nb_id, video_source_ids)
        if video_source_ids
        else {}
    )

    batch_id = f"{topic_id}-numerical-v{NUMERICAL_PROMPT_VERSION}"
    now = datetime.now(UTC)

    # Initial verification pass
    seen_stems: set[str] = set()
    first_pass_results: list[dict[str, Any]] = []
    failed_items_for_retry: list[dict[str, Any]] = []

    for idx, item in enumerate(gen_output.questions, start=1):
        qid = f"{topic_id}-numerical-v{NUMERICAL_PROMPT_VERSION}-{idx:02d}"
        is_valid, reason = validate_numerical_item(item, len(passages), seen_stems)
        if is_valid:
            seen_stems.add(item.stem.strip().lower())

        citations = resolve_question_citations(
            item.sources, passages, sources_summary, video_meta
        )

        if not is_valid:
            pass_info = {
                "idx": idx,
                "qid": qid,
                "item": item,
                "is_valid": False,
                "reject_reason": reason,
                "citations": citations,
                "res": None,
                "solution_code": item.solution_code,
                "stated_answer": item.stated_answer,
                "explanation": item.explanation,
            }
            first_pass_results.append(pass_info)
            continue

        res = verify_numerical(item.solution_code, item.stated_answer, timeout_s=10)

        pass_info = {
            "idx": idx,
            "qid": qid,
            "item": item,
            "is_valid": True,
            "reject_reason": None,
            "citations": citations,
            "res": res,
            "solution_code": item.solution_code,
            "stated_answer": item.stated_answer,
            "explanation": item.explanation,
        }
        first_pass_results.append(pass_info)

        if not res.passed and citations:
            failed_items_for_retry.append({
                "index": idx,
                "stem": item.stem,
                "stated_answer": item.stated_answer,
                "solution_code": item.solution_code,
                "error_detail": res.detail,
            })

    models_used = [gen_model]

    # 2. Single retry call for all failed items in topic
    retry_map: dict[int, Any] = {}
    retry_call_failed = False
    retry_fail_exc_name = ""

    if failed_items_for_retry:
        retry_sys, retry_contents = build_numerical_retry_prompt(
            topic_id=topic_id,
            failed_items=failed_items_for_retry,
            passages_str=passages_str,
        )
        try:
            retry_output, retry_model = generate_json_with_model(
                prompt_version=NUMERICAL_PROMPT_VERSION,
                system_instruction=retry_sys,
                contents=retry_contents,
                schema=NumericalRetryBatch,
                cache_key_parts=[nb_id, topic_id, "num-retry"],
                models=configured_chain,
                on_attempt=on_attempt,
                max_output_tokens=8192,
                attempt_timeout_s=120.0,
                deadline_s=300.0,
            )
            models_used.append(retry_model)
            retry_map = {r.index: r for r in retry_output.items}
        except (ModelUnavailable, UnreadableOutput) as exc:
            retry_call_failed = True
            retry_fail_exc_name = exc.__class__.__name__

    # 3. Assemble StoredQuestions
    stored_pairs: list[tuple[str, StoredQuestion]] = []
    verified_count = 0
    rejected_count = 0
    quarantined_count = 0
    rejected_details: list[str] = []

    for pass_info in first_pass_results:
        idx = pass_info["idx"]
        qid = pass_info["qid"]
        item: GeneratedNumericalItem = pass_info["item"]
        citations = pass_info["citations"]
        res = pass_info["res"]

        if not pass_info.get("is_valid", True):
            status = "rejected"
            passed = False
            detail = f"rejected: {pass_info['reject_reason']}"
            stated_for_storage = item.stated_answer or ""
            code_for_storage = item.solution_code or ""
            computed_val = None
        elif not citations:
            status = "rejected"
            passed = False
            detail = "rejected: no valid citations"
            stated_for_storage = item.stated_answer
            code_for_storage = item.solution_code
            computed_val = res.computed
        elif res.passed:
            status = "verified"
            passed = True
            detail = res.detail
            stated_for_storage = item.stated_answer
            code_for_storage = item.solution_code
            computed_val = res.computed
        else:
            # First verification failed, check retry outcome
            first_detail = res.detail
            if retry_call_failed:
                status = "rejected"
                passed = False
                detail = f"{first_detail} | retry: call failed: {retry_fail_exc_name}"
                stated_for_storage = item.stated_answer
                code_for_storage = item.solution_code
                computed_val = res.computed
            elif idx in retry_map:
                retried_item = retry_map[idx]
                r_code = retried_item.solution_code
                r_stated = retried_item.stated_answer
                retry_res = verify_numerical(r_code, r_stated, timeout_s=10)

                if retry_res.passed:
                    status = "verified"
                    passed = True
                    detail = retry_res.detail
                    stated_for_storage = r_stated
                    code_for_storage = r_code
                    computed_val = retry_res.computed
                else:
                    status = "rejected"
                    passed = False
                    detail = f"{first_detail} | retry: {retry_res.detail}"
                    stated_for_storage = r_stated
                    code_for_storage = r_code
                    computed_val = retry_res.computed
            else:
                # Omitted in retry output
                status = "rejected"
                passed = False
                detail = f"{first_detail} | retry: omitted from retry batch"
                stated_for_storage = item.stated_answer
                code_for_storage = item.solution_code
                computed_val = res.computed

        # Fallback value resolution for numerical answer.value
        if status == "verified":
            final_val = computed_val or stated_for_storage
            if qid in QUARANTINE:
                status = "rejected"
                passed = False
                detail = f"quarantined: {QUARANTINE[qid]} | original: {detail}"
                quarantined_count += 1
                rejected_count += 1
                rejected_details.append(f"{qid}: {detail}")
            else:
                verified_count += 1
        else:
            if parse_exact(stated_for_storage) is not None:
                final_val = stated_for_storage
            elif computed_val is not None and parse_exact(computed_val) is not None:
                final_val = computed_val
            else:
                final_val = "0"
            rejected_count += 1
            if pass_info.get("is_valid", True) and citations:
                detail_with_vals = (
                    f"{detail} (stated: '{stated_for_storage}', computed: '{computed_val}')"
                )
                detail = detail_with_vals
            rejected_details.append(f"{qid}: {detail}")

        q_doc = StoredQuestion(
            type="numerical",
            topic_id=topic_id,
            difficulty=max(1, min(3, item.difficulty)),
            stem=item.stem,
            options=[],
            answer=QuestionAnswer(value=final_val),
            explanation=item.explanation,
            citations=citations,
            verification=QuestionVerification(
                method="python",
                passed=passed,
                detail=detail,
            ),
            status=status,
            batch_id=batch_id,
            solution_code=code_for_storage,
            created_at=now,
        )
        stored_pairs.append((qid, q_doc))

    report = {
        "topic_id": topic_id,
        "type": "numerical",
        "generated": len(stored_pairs),
        "verified": verified_count,
        "rejected": rejected_count,
        "quarantined_applied": quarantined_count,
        "rejection_details": rejected_details,
        "generator_model": gen_model,
        "checker_model": None,
        "models_used": models_used,
    }
    return stored_pairs, report
