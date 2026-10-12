"""Seed production Firestore (study-companion-d049d) from local emulator.

Pure migration script: copies demo notebook, sources, chunks, topics,
questions, and pre-warmed llm_cache from the local emulator (127.0.0.1:8080)
to live Firestore using service account credentials.

Zero Gemini calls. Zero embedding calculations.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import Any

import google.auth.credentials
from google.cloud import firestore
from google.oauth2 import service_account


def resolve_credentials(explicit_path: str | None) -> str:
    """Resolve path to service account credentials JSON."""
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path

    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path and os.path.isfile(env_path):
        return env_path

    spare_dir = r"C:\personal\shreyas\hackathons\multimodal hackathon spare files"
    candidates = glob.glob(
        os.path.join(spare_dir, "study-companion-d049d-firebase-adminsdk-*.json")
    )
    if candidates and os.path.isfile(candidates[0]):
        return candidates[0]

    raise FileNotFoundError(
        "Could not find service account credentials. "
        "Pass --credentials <path> or set GOOGLE_APPLICATION_CREDENTIALS."
    )


def get_emulator_client(emulator_host: str) -> firestore.Client:
    """Connect directly to the Firestore emulator via unencrypted transport."""
    os.environ["FIRESTORE_EMULATOR_HOST"] = emulator_host
    client = firestore.Client(
        project="demo-study-companion",
        credentials=google.auth.credentials.AnonymousCredentials(),
    )
    _ = client._firestore_api
    return client


def get_production_client(project_id: str, creds_path: str) -> firestore.Client:
    """Connect to live production Firestore using service account credentials."""
    os.environ.pop("FIRESTORE_EMULATOR_HOST", None)
    creds = service_account.Credentials.from_service_account_file(creds_path)
    client = firestore.Client(project=project_id, credentials=creds)
    _ = client._firestore_api
    return client


def migrate_collection_batch(
    docs_to_write: list[tuple[firestore.DocumentReference, dict[str, Any]]],
    prod_client: firestore.Client,
    batch_size: int = 400,
) -> int:
    """Commit documents to production Firestore in sequential batches."""
    total_written = 0
    for i in range(0, len(docs_to_write), batch_size):
        batch = prod_client.batch()
        batch_slice = docs_to_write[i : i + batch_size]
        for ref, data in batch_slice:
            batch.set(ref, data)
        batch.commit()
        total_written += len(batch_slice)
        print(f"  [batch] Committed {total_written}/{len(docs_to_write)} documents...")
    return total_written


def run_seeder(
    notebook_id: str,
    emulator_host: str,
    target_project: str,
    creds_path: str,
    dry_run: bool,
) -> int:
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
        subcoll_data[sub] = [(d.id, d.to_dict() or {}) for d in docs]
        print(f"  Found {len(docs)} documents in '{sub}' subcollection.")

    # 3. Read top-level llm_cache
    cache_docs = list(emu_client.collection("llm_cache").stream())
    cache_data = [(d.id, d.to_dict() or {}) for d in cache_docs]
    print(f"  Found {len(cache_docs)} documents in top-level 'llm_cache'.")

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

    os.environ.pop("FIRESTORE_EMULATOR_HOST", None)
    print(f"\nConnecting to production Firestore ({target_project})...")
    prod_client = get_production_client(target_project, creds_path)

    # Prepare document list to write
    staged: list[tuple[firestore.DocumentReference, dict[str, Any]]] = []

    # Notebook
    target_nb_ref = prod_client.collection("notebooks").document(notebook_id)
    staged.append((target_nb_ref, nb_data))

    # Subcollections
    for sub in subcollections:
        target_coll = target_nb_ref.collection(sub)
        for doc_id, data in subcoll_data[sub]:
            staged.append((target_coll.document(doc_id), data))

    # llm_cache
    target_cache_coll = prod_client.collection("llm_cache")
    for doc_id, data in cache_data:
        staged.append((target_cache_coll.document(doc_id), data))

    print(f"Writing {len(staged)} documents to production in batches...")
    written = migrate_collection_batch(staged, prod_client, batch_size=400)
    print(f"Migration complete: {written} documents committed.")

    # 4. Post-migration verification
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
    args = parser.parse_args()

    try:
        creds_path = resolve_credentials(args.credentials)
        print(f"Resolved credentials: {creds_path}")
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