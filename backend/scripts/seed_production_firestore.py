"""Seed production Firestore (study-companion-d049d) from local emulator.

Pure migration script: copies demo notebook, sources, chunks, topics,
questions, and pre-warmed llm_cache from the local emulator (127.0.0.1:8080)
to live Firestore using service account credentials.

Zero Gemini calls. Zero embedding calculations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import google.auth.credentials
from google.cloud import firestore
from google.oauth2 import service_account

# Ensure backend root is on sys.path so 'app' can be imported
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.questions.bank import QUARANTINE

REQUIRED_PROJECT_ID = "study-companion-d049d"


def validate_emulator_host(emulator_host: str) -> None:
    """Enforce that emulator host points to a local address (127.0.0.1 or localhost)."""
    host_clean = emulator_host
    if "://" in host_clean:
        host_clean = host_clean.split("://", 1)[1]
    host_part = host_clean.split(":")[0].strip().lower()
    if host_part not in ("127.0.0.1", "localhost"):
        raise ValueError(
            f"Emulator host must point to a local address ('127.0.0.1' or 'localhost'). "
            f"Got: '{emulator_host}'"
        )


def resolve_credentials(explicit_path: str | None) -> str:
    """Resolve path to service account credentials JSON without fallback globbing."""
    if explicit_path:
        if not os.path.isfile(explicit_path):
            raise FileNotFoundError("Specified credentials file does not exist.")
        creds_path = explicit_path
    elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        env_path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
        if not os.path.isfile(env_path):
            raise FileNotFoundError(
                "GOOGLE_APPLICATION_CREDENTIALS points to a file that does not exist."
            )
        creds_path = env_path
    else:
        raise FileNotFoundError(
            "Could not find service account credentials. "
            "Pass --credentials <path> or set GOOGLE_APPLICATION_CREDENTIALS."
        )

    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            cred_data = json.load(f)
    except Exception:
        raise ValueError("Failed to parse service account JSON file.")

    if cred_data.get("project_id") != REQUIRED_PROJECT_ID:
        raise ValueError(
            f"Service account project_id mismatch: expected '{REQUIRED_PROJECT_ID}', "
            f"got '{cred_data.get('project_id')}'"
        )

    return creds_path


def get_emulator_client(emulator_host: str) -> firestore.Client:
    """Connect directly to the Firestore emulator via unencrypted transport."""
    validate_emulator_host(emulator_host)
    os.environ["FIRESTORE_EMULATOR_HOST"] = emulator_host
    client = firestore.Client(
        project="demo-study-companion",
        credentials=google.auth.credentials.AnonymousCredentials(),
    )
    _ = client._firestore_api
    return client


def get_production_client(project_id: str, creds_path: str) -> firestore.Client:
    """Connect to live production Firestore using service account credentials."""
    if project_id != REQUIRED_PROJECT_ID:
        raise ValueError(
            f"Target project must strictly equal '{REQUIRED_PROJECT_ID}'. Got: '{project_id}'"
        )

    os.environ.pop("FIRESTORE_EMULATOR_HOST", None)
    assert os.environ.get("FIRESTORE_EMULATOR_HOST") is None, (
        "FIRESTORE_EMULATOR_HOST must be cleared before connecting to production."
    )

    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            cred_data = json.load(f)
    except Exception:
        raise ValueError("Failed to parse service account JSON file.")

    if cred_data.get("project_id") != REQUIRED_PROJECT_ID:
        raise ValueError(
            f"Service account project_id mismatch: expected '{REQUIRED_PROJECT_ID}', "
            f"got '{cred_data.get('project_id')}'"
        )

    creds = service_account.Credentials.from_service_account_file(creds_path)
    client = firestore.Client(project=project_id, credentials=creds)
    _ = client._firestore_api
    return client


def validate_emulator_data(
    nb_data: dict[str, Any],
    subcoll_data: dict[str, list[tuple[str, dict[str, Any]]]],
) -> None:
    """Pre-flight data validation on emulator contents before staging batches."""
    # 1. Notebook is_demo
    assert nb_data.get("is_demo") is True, (
        f"Expected nb_data['is_demo'] to be True, got {nb_data.get('is_demo')}"
    )

    # 2. Sources validation: exactly 26 sources, all status == 'ready'
    sources = subcoll_data.get("sources", [])
    assert len(sources) == 26, (
        f"Expected exactly 26 sources, found {len(sources)}"
    )
    for src_id, src_data in sources:
        assert src_data.get("status") == "ready", (
            f"Source '{src_id}' status is '{src_data.get('status')}', expected 'ready'"
        )

    # 3. Topics validation: t1..t6 (and other if present)
    topics_map = dict(subcoll_data.get("topics", []))
    for t_id in ("t1", "t2", "t3", "t4", "t5", "t6"):
        assert t_id in topics_map, f"Required topic '{t_id}' missing from emulator"
        t_data = topics_map[t_id]
        locs = t_data.get("locations") or []
        assert len(locs) > 0, f"Topic '{t_id}' has empty locations"
        assert t_data.get("location_count") == len(locs), (
            f"Topic '{t_id}' location_count mismatch: {t_data.get('location_count')} != {len(locs)}"
        )

    if "other" in topics_map:
        other_data = topics_map["other"]
        other_locs = other_data.get("locations") or []
        assert other_data.get("location_count") == len(other_locs), (
            f"Topic 'other' location_count mismatch: {other_data.get('location_count')} != {len(other_locs)}"
        )

    # 4. Chunks validation: count > 0, embedding length == 384
    chunks = subcoll_data.get("chunks", [])
    assert len(chunks) > 0, "Chunks count must be greater than 0"
    for chunk_id, chunk_data in chunks:
        emb = chunk_data.get("embedding")
        assert emb is not None and len(emb) == 384, (
            f"Chunk '{chunk_id}' embedding length is {len(emb) if emb is not None else None}, expected 384"
        )


def migrate_collection_batch(
    docs_to_write: list[
        tuple[firestore.DocumentReference, dict[str, Any]]
        | tuple[firestore.DocumentReference, dict[str, Any], bool]
    ],
    prod_client: firestore.Client,
    batch_size: int = 400,
) -> int:
    """Commit documents to production Firestore in sequential batches."""
    total_written = 0
    for i in range(0, len(docs_to_write), batch_size):
        batch = prod_client.batch()
        batch_slice = docs_to_write[i : i + batch_size]
        for item in batch_slice:
            if len(item) == 3:
                ref, data, merge = item  # type: ignore[misc]
                batch.set(ref, data, merge=merge)
            else:
                ref, data = item  # type: ignore[misc]
                batch.set(ref, data)
        batch.commit()
        total_written += len(batch_slice)
        print(f"  [batch] Committed {total_written}/{len(docs_to_write)} documents...")
    return total_written


def run_seeder(
    notebook_id: str,
    emulator_host: str,
    target_project: str,
    creds_path: str | None,
    dry_run: bool,
) -> int:
    if target_project != REQUIRED_PROJECT_ID:
        print(f"ERROR: Target project must strictly equal '{REQUIRED_PROJECT_ID}'. Got: '{target_project}'")
        return 1

    validate_emulator_host(emulator_host)

    os.environ["FIRESTORE_EMULATOR_HOST"] = emulator_host
    print(f"Connecting to emulator at {emulator_host}...")
    emu_client = get_emulator_client(emulator_host)

    # 1. Read demo notebook document
    nb_ref = emu_client.collection("notebooks").document(notebook_id)
    nb_snap = nb_ref.get()
    if not nb_snap.exists:
        print(f"ERROR: Notebook '{notebook_id}' does not exist in emulator Firestore.")
        return 1

    nb_data = nb_snap.to_dict() or {}
    print(f"Found notebook '{notebook_id}' (status: {nb_data.get('status')})")

    # 2. Read subcollections under demo notebook
    subcollections = ["sources", "chunks", "topics", "questions"]
    subcoll_data: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    for sub in subcollections:
        docs = list(nb_ref.collection(sub).stream())
        if sub == "questions":
            skipped_quarantined = 0
            skipped_rejected = 0
            skipped_other = 0
            filtered_questions: list[tuple[str, dict[str, Any]]] = []

            for d in docs:
                qid = d.id
                qdata = d.to_dict() or {}
                if qid in QUARANTINE:
                    skipped_quarantined += 1
                    continue
                status = qdata.get("status")
                if status != "verified":
                    if status == "rejected":
                        skipped_rejected += 1
                    else:
                        skipped_other += 1
                    continue
                filtered_questions.append((qid, qdata))

            verified_count = len(filtered_questions)
            subcoll_data["questions"] = filtered_questions
            print(f"  Found {len(docs)} documents in 'questions' subcollection.")
            print(
                f"  Filtered questions: {verified_count} verified kept, "
                f"{skipped_quarantined} quarantined skipped, "
                f"{skipped_rejected} rejected skipped"
                + (f", {skipped_other} other skipped" if skipped_other else "")
                + "."
            )
        else:
            subcoll_data[sub] = [(d.id, d.to_dict() or {}) for d in docs]
            print(f"  Found {len(docs)} documents in '{sub}' subcollection.")

    # 3. Read top-level llm_cache
    cache_docs = list(emu_client.collection("llm_cache").stream())
    cache_data = [(d.id, d.to_dict() or {}) for d in cache_docs]
    print(f"  Found {len(cache_docs)} documents in top-level 'llm_cache'.")

    # 4. Pre-flight emulator validation
    validate_emulator_data(nb_data, subcoll_data)

    # 5. Update notebook counts map
    verified_count = len(subcoll_data["questions"])
    chunks_count = len(subcoll_data["chunks"])
    if "counts" not in nb_data or not isinstance(nb_data["counts"], dict):
        nb_data["counts"] = {}
    nb_data["counts"]["questions_verified"] = verified_count
    nb_data["counts"]["chunks"] = chunks_count
    nb_data["counts"].pop("items", None)

    # Summary table
    print("\n--- Seeding Summary ---")
    print(f"Target Project: {target_project}")
    print(f"Notebook:       {notebook_id} (1 document)")
    for sub in subcollections:
        print(f"Subcollection:  {sub:<10} ({len(subcoll_data[sub])} documents)")
    print(f"Top-level:      llm_cache  ({len(cache_data)} documents)")
    total_docs = 1 + sum(len(v) for v in subcoll_data.values()) + len(cache_data)
    print(f"Total Docs:     {total_docs}")

    if dry_run:
        print("\n[DRY RUN] Complete. No documents written to production.")
        return 0

    if not creds_path:
        print("ERROR: Production credentials required for live write.")
        return 1

    os.environ.pop("FIRESTORE_EMULATOR_HOST", None)
    assert os.environ.get("FIRESTORE_EMULATOR_HOST") is None, (
        "FIRESTORE_EMULATOR_HOST must be cleared before connecting to production."
    )
    print(f"\nConnecting to production Firestore ({target_project})...")
    prod_client = get_production_client(target_project, creds_path)

    # Remove any stale/unverified questions in production to maintain bank integrity
    target_nb_ref = prod_client.collection("notebooks").document(notebook_id)
    target_q_coll = target_nb_ref.collection("questions")
    prod_q_refs = list(target_q_coll.list_documents())
    valid_q_ids = {qid for qid, _ in subcoll_data["questions"]}
    stale_q_refs = [ref for ref in prod_q_refs if ref.id not in valid_q_ids]
    if stale_q_refs:
        print(f"  Removing {len(stale_q_refs)} stale/unverified questions from production...")
        for i in range(0, len(stale_q_refs), 400):
            del_batch = prod_client.batch()
            for ref in stale_q_refs[i : i + 400]:
                del_batch.delete(ref)
            del_batch.commit()

    # Prepare document list to write
    staged: list[tuple[firestore.DocumentReference, dict[str, Any], bool]] = []

    # Notebook with merge=True
    staged.append((target_nb_ref, nb_data, True))

    # Subcollections with merge=False
    for sub in subcollections:
        target_coll = target_nb_ref.collection(sub)
        for doc_id, data in subcoll_data[sub]:
            staged.append((target_coll.document(doc_id), data, False))

    # llm_cache with merge=False
    target_cache_coll = prod_client.collection("llm_cache")
    for doc_id, data in cache_data:
        staged.append((target_cache_coll.document(doc_id), data, False))

    print(f"Writing {len(staged)} documents to production in batches...")
    written = migrate_collection_batch(staged, prod_client, batch_size=400)
    print(f"Migration complete: {written} documents committed.")

    # Post-migration verification
    print("\nVerifying production document counts...")
    prod_nb_snap = target_nb_ref.get()
    if not prod_nb_snap.exists:
        print("VERIFICATION FAILED: Target notebook does not exist.")
        return 1

    mismatches = 0
    for sub in subcollections:
        prod_count = len(list(target_nb_ref.collection(sub).list_documents()))
        expected = len(subcoll_data[sub])
        status = "OK" if prod_count == expected else "MISMATCH"
        if status != "OK":
            mismatches += 1
        print(f"  {sub:<10}: expected={expected}, found={prod_count} [{status}]")

    prod_cache_count = len(list(target_cache_coll.list_documents()))
    cache_status = "OK" if prod_cache_count == len(cache_data) else "MISMATCH"
    if cache_status != "OK":
        mismatches += 1
    print(
        f"  llm_cache : expected={len(cache_data)}, found={prod_cache_count} [{cache_status}]"
    )

    if mismatches > 0:
        print(f"\nVERIFICATION FAILED: {mismatches} collection count mismatches.")
        return 1

    print("\nAll production counts match the emulator. Seeding successful.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate demo course from local emulator to production Firestore."
    )
    parser.add_argument("--notebook-id", default="nb_demo_6041")
    parser.add_argument("--emulator-host", default="127.0.0.1:8080")
    parser.add_argument("--target-project", default="study-companion-d049d")
    parser.add_argument("--credentials", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect counts without writing to production",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm live writes to production",
    )
    args = parser.parse_args()

    # 1. Target project guard
    if args.target_project != REQUIRED_PROJECT_ID:
        print(
            f"ERROR: Target project must strictly equal '{REQUIRED_PROJECT_ID}'. Got: '{args.target_project}'."
        )
        sys.exit(1)

    # 2. Emulator host guard
    try:
        validate_emulator_host(args.emulator_host)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # 3. Confirmation gate
    if not args.dry_run and not args.yes:
        print("Refusing to write to production without --yes confirmation.")
        sys.exit(1)

    # 4. Resolve credentials only when NOT in dry-run
    creds_path: str | None = None
    if not args.dry_run:
        try:
            creds_path = resolve_credentials(args.credentials)
            print("Production credentials resolved successfully.")
        except Exception as e:
            print(f"Error resolving credentials: {e}")
            sys.exit(1)

    code = run_seeder(
        notebook_id=args.notebook_id,
        emulator_host=args.emulator_host,
        target_project=args.target_project,
        creds_path=creds_path,
        dry_run=args.dry_run,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
