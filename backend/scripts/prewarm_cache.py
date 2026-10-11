"""Pre-warm LLM response cache for demo notebook questions.

Usage:
    uv run --env-file ../.env python scripts/prewarm_cache.py --dry-run
    uv run --env-file ../.env python scripts/prewarm_cache.py --limit 5
    uv run --env-file ../.env python scripts/prewarm_cache.py --topic t1
    uv run --env-file ../.env python scripts/prewarm_cache.py --delay 12.0
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# Ensure backend/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import run_answer_pipeline first to prevent circular import via app.chat
from app.chat.answer import run_answer_pipeline  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.client import get_db  # noqa: E402
from app.db.notebooks import DEMO_NOTEBOOK_ID, get_notebook_snapshot  # noqa: E402
from app.db.paths import llm_cache_path  # noqa: E402
from app.llm.prompts.ask import (  # noqa: E402
    PROMPT_VERSION,
    build_contents,
    build_system_instruction,
)
from app.models.notebook import Notebook  # noqa: E402
from app.retrieval.search import RetrievedChunk, search  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("prewarm_cache")


def load_demo_notebook(notebook_id: str = DEMO_NOTEBOOK_ID) -> Notebook:
    """Load notebook directly via Firestore snapshot, raising if missing."""
    doc = get_notebook_snapshot(notebook_id)
    if not doc.exists:
        raise RuntimeError(f"Demo notebook {notebook_id} not found in Firestore.")
    return Notebook.model_validate({"id": doc.id, **(doc.to_dict() or {})})


def load_and_filter_testset(
    testset_path: Path,
    topic: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Load testset cases and filter by topic and limit."""
    if not testset_path.exists():
        raise FileNotFoundError(f"Testset file not found at {testset_path}")

    with open(testset_path, encoding="utf-8") as f:
        cases: list[dict[str, Any]] = json.load(f)

    if topic is not None:
        if topic == "other":
            cases = [c for c in cases if c.get("topic_id") is None or c.get("topic_id") == "other"]
        else:
            cases = [c for c in cases if c.get("topic_id") == topic]

    if limit is not None and limit > 0:
        cases = cases[:limit]

    return cases


def compute_target_cache_keys(
    notebook: Notebook,
    question: str,
    models: list[str],
    allow_outside: bool = False,
) -> tuple[list[tuple[str, str]], list[RetrievedChunk]]:
    """Derive byte-for-byte target cache keys and retrieved chunks for a question."""
    chunks = search(notebook, question, topic_id=None)

    sources_by_id = {s.source_id: s for s in notebook.sources_summary}
    chunks_data = []
    for idx, c in enumerate(chunks, start=1):
        s = sources_by_id.get(c.source_id)
        s_title = s.title if s else c.source_id
        chunks_data.append((idx, s_title, c.loc.page, c.text, c.loc.t_start_s))

    system_instruction = build_system_instruction(
        allow_outside=allow_outside,
        has_history=False,
        has_preferences=False,
    )
    contents = build_contents(
        question,
        chunks_data,
        custom_instructions=None,
        history=None,
    )
    cache_key_parts = [
        str(allow_outside).lower(),
        ",".join(c.chunk_id for c in chunks),
        question.strip(),
    ]
    prompt_hash = hashlib.sha256(f"{system_instruction}\n\n{contents}".encode()).hexdigest()

    keys: list[tuple[str, str]] = []
    for model in models:
        parts = [model, PROMPT_VERSION, prompt_hash, *(str(p) for p in cache_key_parts)]
        key = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
        keys.append((model, key))

    return keys, chunks


def check_cache_exists(
    db: Any,
    keys: list[tuple[str, str]],
    disk_cache: dict[str, Any] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Check if any model key exists in Firestore or local disk cache."""
    for model, key in keys:
        doc_ref = db.document(llm_cache_path(key))
        if doc_ref.get().exists:
            return True, model, key
        if disk_cache and key in disk_cache:
            return True, model, key
    return False, None, None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Pre-warm LLM response cache for demo notebook questions."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate chunk retrieval and cache keys without calling live LLMs",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Cap questions processed (default: all 25)",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        choices=["t1", "t2", "t3", "t4", "t5", "t6", "other"],
        help="Filter to questions of a specific topic (t1 to t6, or other)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=12.0,
        help="Configurable pacing sleep in seconds between live calls (default: 12.0s)",
    )
    parser.add_argument(
        "--notebook",
        type=str,
        default=DEMO_NOTEBOOK_ID,
        help=f"Target notebook ID (default: {DEMO_NOTEBOOK_ID})",
    )
    parser.add_argument(
        "--testset",
        type=str,
        default=None,
        help="Optional custom path to evaluation testset JSON",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Execute pre-warming or dry-run workflow."""
    args = parse_args(argv)
    settings = get_settings()
    db = get_db()
    models = settings.gemini_model_chain

    # Determine testset path
    if args.testset:
        testset_path = Path(args.testset).resolve()
    else:
        # Default: eval/testset.json at repo root
        testset_path = (Path(__file__).resolve().parents[2] / "eval" / "testset.json").resolve()

    logger.info("Loading demo notebook: %s", args.notebook)
    notebook = load_demo_notebook(args.notebook)

    logger.info("Loading questions from: %s", testset_path)
    cases = load_and_filter_testset(testset_path, topic=args.topic, limit=args.limit)
    logger.info(
        "Selected %d questions (topic filter: %s, limit: %d)",
        len(cases),
        args.topic or "all",
        args.limit,
    )

    if args.dry_run:
        print("\n=== DRY RUN: Cache Pre-Warming Evaluation ===")
        print(f"Notebook: {notebook.id} ({notebook.name})")
        print(f"Model Chain: {models}")
        print(f"Prompt Version: {PROMPT_VERSION}")
        print(f"Questions to evaluate: {len(cases)}\n")

        total_hits = 0
        total_misses = 0

        for idx, case in enumerate(cases, start=1):
            qid = case.get("id", f"case-{idx}")
            topic_id = case.get("topic_id") or "other"
            question = case.get("question", "")

            keys, chunks = compute_target_cache_keys(
                notebook=notebook,
                question=question,
                models=models,
                allow_outside=False,
            )
            is_hit, hit_model, hit_key = check_cache_exists(db, keys)

            primary_key = keys[0][1] if keys else "unknown"
            chunk_ids = [c.chunk_id for c in chunks]

            status_str = f"HIT ({hit_model})" if is_hit else "MISS"
            if is_hit:
                total_hits += 1
            else:
                total_misses += 1

            print(f"[{idx:02d}/{len(cases):02d}] {qid} (topic: {topic_id}) -> {status_str}")
            print(f"     Question: {question[:75]}...")
            print(f"     Retrieved chunks ({len(chunks)}): {chunk_ids}")
            print(f"     Target cache key ({models[0]}): {primary_key}")
            if is_hit:
                print(f"     Hit cache key: {hit_key}")
            print()

        print("=== DRY RUN SUMMARY ===")
        print(f"Total Evaluated: {len(cases)}")
        print(f"Cache Hits:      {total_hits}")
        print(f"Cache Misses:    {total_misses}")
        print("Dry run completed successfully with status 0 (0 live LLM calls made).\n")
        return 0

    # Live Pre-Warming Execution
    print("\n=== LIVE: Cache Pre-Warming Execution ===")
    print(f"Notebook: {notebook.id} ({notebook.name})")
    print(f"Model Chain: {models}")
    print(f"Questions: {len(cases)}")
    print(f"Pacing delay: {args.delay:.1f}s between live calls\n")

    warmed_count = 0
    hit_count = 0

    for idx, case in enumerate(cases, start=1):
        qid = case.get("id", f"case-{idx}")
        topic_id = case.get("topic_id") or "other"
        question = case.get("question", "")

        keys, chunks = compute_target_cache_keys(
            notebook=notebook,
            question=question,
            models=models,
            allow_outside=False,
        )
        is_hit, hit_model, hit_key = check_cache_exists(db, keys)

        if is_hit:
            hit_count += 1
            print(
                f"[{idx:02d}/{len(cases):02d}] [CACHE HIT] {qid} ({topic_id}) "
                f"already warm in cache ({hit_model})."
            )
        else:
            print(
                f"[{idx:02d}/{len(cases):02d}] [CACHE MISS] {qid} ({topic_id}) "
                "cold cache. Calling run_answer_pipeline..."
            )
            # Direct invocation rule: call run_answer_pipeline directly
            paragraphs, context, model_name, latency_ms = run_answer_pipeline(
                notebook=notebook,
                question=question,
                allow_outside=False,
            )
            warmed_count += 1
            print(
                f"     -> Pre-warmed successfully via {model_name} in {latency_ms} ms "
                f"({len(paragraphs)} paragraphs, {len(context)} context chunks)."
            )

            # Pacing delay after live LLM call if more questions remain
            if idx < len(cases) and args.delay > 0:
                print(f"     -> Pacing: sleeping {args.delay:.1f}s...")
                time.sleep(args.delay)

    print("\n=== PRE-WARM SUMMARY ===")
    print(f"Total Processed: {len(cases)}")
    print(f"Existing Hits:   {hit_count}")
    print(f"Newly Warmed:    {warmed_count}")
    print("Pre-warming workflow completed successfully.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
