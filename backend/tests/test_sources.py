import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pymupdf
from fastapi.testclient import TestClient
from fastembed import TextEmbedding
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

from app.db import chunks_collection_path, get_db, notebook_path
from app.db.notebooks import DEMO_NOTEBOOK_ID
from app.db.sources import create_source_transaction, generate_source_id
from app.embeddings import count_tokens, embed_passages, embed_query
from app.main import app
from tests.conftest import create_emulator_user

client = TestClient(app)


def create_pdf(page_texts: list[str], height: float = 792) -> bytes:
    """Create a synthetic PDF with PyMuPDF."""
    with pymupdf.open() as doc:
        for text in page_texts:
            page = doc.new_page(width=612, height=height)
            if text:
                page.insert_text((50, 72), text)
        return doc.tobytes()


def create_test_notebook(token: str) -> str:
    """Helper to create a fresh notebook."""
    res = client.post(
        "/v1/notebooks",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"Test NB {uuid4().hex[:6]}"},
    )
    assert res.status_code == 201
    return res.json()["id"]


def test_source_upload_success_and_metadata(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    pdf_bytes = create_pdf([
        "Page one content: Linear algebra is mathematics concerning linear equations.",
        "Page two content: Vectors and matrices are the objects in linear algebra.",
        "Page three content: Eigenvalues provide insight into transformations.",
    ])

    files = {"file": ("linear_algebra_intro.pdf", BytesIO(pdf_bytes), "application/pdf")}
    data = {"role": "content"}

    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
        data=data,
    )
    assert res.status_code == 202
    src_data = res.json()

    assert src_data["title"] == "linear_algebra_intro"
    assert src_data["ref_n"] == 1
    assert src_data["kind"] == "pdf"
    assert src_data["role"] == "content"
    assert src_data["status"] == "ready"
    assert src_data["stage"] == "done"
    assert src_data["page_count"] == 3
    assert src_data["error"] is None

    # Verify Firestore chunks
    db = get_db()
    chunks_query = db.collection(chunks_collection_path(nb_id)).order_by("loc.page")
    chunks = list(chunks_query.stream())
    assert len(chunks) >= 3

    pages_seen = set()
    for idx, c in enumerate(chunks):
        c_dict = c.to_dict()
        assert c.id == f"{src_data['id']}-{idx:05d}"
        assert c_dict["source_id"] == src_data["id"]
        assert c_dict["kind"] == "text"
        assert c_dict["token_count"] <= 400
        assert isinstance(c_dict["embedding"], Vector)
        assert len(c_dict["embedding"]) == 384
        pages_seen.add(c_dict["loc"]["page"])

    assert pages_seen == {1, 2, 3}

    # Verify notebook document update
    nb_doc = db.document(notebook_path(nb_id)).get()
    nb_dict = nb_doc.to_dict()
    assert nb_dict["status"] == "ready"
    assert nb_dict["counts"]["chunks"] == len(chunks)
    assert len(nb_dict["sources_summary"]) == 1
    assert nb_dict["sources_summary"][0]["status"] == "ready"
    assert nb_dict["sources_summary"][0]["ref_n"] == 1


def test_source_second_upload_gets_ref_n_2(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    pdf1 = create_pdf(["Content 1"])
    pdf2 = create_pdf(["Content 2"])

    res1 = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("first.pdf", BytesIO(pdf1), "application/pdf")},
    )
    assert res1.status_code == 202
    assert res1.json()["ref_n"] == 1

    res2 = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("second.pdf", BytesIO(pdf2), "application/pdf")},
    )
    assert res2.status_code == 202
    assert res2.json()["ref_n"] == 2


def test_source_heavy_page_produces_multiple_chunks(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    # 1500 words on a single page
    heavy_text = "\n".join([
        f"Sentence {i} contains important concepts in probability theory and statistics."
        for i in range(120)
    ])
    pdf_bytes = create_pdf([heavy_text], height=3000)

    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("heavy.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res.status_code == 202
    src_data = res.json()
    assert src_data["status"] == "ready"

    db = get_db()
    chunks = list(db.collection(chunks_collection_path(nb_id)).stream())
    assert len(chunks) > 1
    for c in chunks:
        c_dict = c.to_dict()
        assert c_dict["loc"]["page"] == 1
        assert c_dict["token_count"] <= 400


def test_source_scanned_pdf_fails_cleanly(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    # 2 pages with no text
    empty_pdf = create_pdf(["", ""])

    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("empty.pdf", BytesIO(empty_pdf), "application/pdf")},
    )
    assert res.status_code == 202
    src_data = res.json()
    assert src_data["status"] == "failed"
    assert src_data["stage"] == "extract"
    assert "This PDF has no extractable text (it may be scanned)." in src_data["error"]

    db = get_db()
    chunks = list(db.collection(chunks_collection_path(nb_id)).stream())
    assert len(chunks) == 0

    nb_dict = db.document(notebook_path(nb_id)).get().to_dict()
    assert nb_dict["status"] != "processing"
    assert nb_dict["status"] == "empty"


def test_source_non_pdf_rejected(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("fake.pdf", BytesIO(b"Not a real PDF header"), "application/pdf")},
    )
    assert res.status_code == 422
    assert res.json()["error"]["message"] == "Only PDF files are supported for now."

    db = get_db()
    sources = list(db.collection(f"notebooks/{nb_id}/sources").stream())
    assert len(sources) == 0


def test_source_oversized_rejected(user_tracker, notebook_tracker, monkeypatch):
    uid, token = create_emulator_user(email=f"user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    monkeypatch.setattr("app.api.sources.MAX_UPLOAD_SIZE_BYTES", 50)
    pdf_bytes = create_pdf(["Sample content exceeding 50 bytes"])

    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("oversized.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res.status_code == 422
    assert res.json()["error"]["message"] == "File is larger than 30 MB."


def test_source_access_control(user_tracker, notebook_tracker, demo_notebook_context):
    uid_a, token_a = create_emulator_user(email=f"user_a_{uuid4().hex[:8]}@example.com")
    uid_b, token_b = create_emulator_user(email=f"user_b_{uuid4().hex[:8]}@example.com")
    user_tracker.extend([uid_a, uid_b])
    nb_a = create_test_notebook(token_a)
    notebook_tracker.append(nb_a)

    pdf_bytes = create_pdf(["User A secret math notes."])
    upload_res = client.post(
        f"/v1/notebooks/{nb_a}/sources",
        headers={"Authorization": f"Bearer {token_a}"},
        files={"file": ("notes.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    src_id = upload_res.json()["id"]

    # Seed demo notebook
    db = get_db()
    db.document(notebook_path(DEMO_NOTEBOOK_ID)).set({
        "name": "Demo Course",
        "owner_uid": "seed-owner",
        "is_demo": True,
        "status": "ready",
        "sources_summary": [],
        "counts": {"chunks": 0, "items": 0, "questions_verified": 0},
        "created_at": datetime.now(UTC),
    })

    # User B uploading to User A's notebook -> 404
    res_b_upload = client.post(
        f"/v1/notebooks/{nb_a}/sources",
        headers={"Authorization": f"Bearer {token_b}"},
        files={"file": ("b.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res_b_upload.status_code == 404
    assert res_b_upload.json()["error"]["code"] == "not_found"

    # User B uploading to demo notebook -> 403
    res_demo_upload = client.post(
        f"/v1/notebooks/{DEMO_NOTEBOOK_ID}/sources",
        headers={"Authorization": f"Bearer {token_b}"},
        files={"file": ("b.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res_demo_upload.status_code == 403
    assert res_demo_upload.json()["error"]["code"] == "forbidden"

    # User B getting User A's source -> 404
    assert client.get(
        f"/v1/notebooks/{nb_a}/sources/{src_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    ).status_code == 404

    # User B streaming User A's file -> 404
    assert client.get(
        f"/v1/notebooks/{nb_a}/sources/{src_id}/file",
        headers={"Authorization": f"Bearer {token_b}"},
    ).status_code == 404


def test_sources_list_and_cursor_pagination(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"list_user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_ids = []
    for i in range(3):
        pdf = create_pdf([f"Content for file {i}"])
        res = client.post(
            f"/v1/notebooks/{nb_id}/sources",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (f"doc_{i}.pdf", BytesIO(pdf), "application/pdf")},
        )
        src_ids.append(res.json()["id"])

    # Default list returns all 3 in ref_n ascending order
    res_list = client.get(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert len(items) == 3
    assert [item["ref_n"] for item in items] == [1, 2, 3]

    # Limit = 2 returns 2 items + next_cursor
    res_p1 = client.get(
        f"/v1/notebooks/{nb_id}/sources?limit=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_p1.status_code == 200
    p1_data = res_p1.json()
    assert len(p1_data["items"]) == 2
    cursor = p1_data["next_cursor"]
    assert cursor == p1_data["items"][-1]["id"]

    # Limit = 2 with cursor returns remaining 1 item
    res_p2 = client.get(
        f"/v1/notebooks/{nb_id}/sources?limit=2&cursor={cursor}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_p2.status_code == 200
    p2_data = res_p2.json()
    assert len(p2_data["items"]) == 1
    assert p2_data["next_cursor"] is None

    # Invalid cursor gives 422
    assert client.get(
        f"/v1/notebooks/{nb_id}/sources?cursor=invalid_cursor_id",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 422


def test_source_file_streaming_and_range_requests(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"file_user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    pdf_bytes = create_pdf(["Hello world! This is a test file for range streaming verification."])
    total_size = len(pdf_bytes)

    res_upload = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("stream_test.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    src_id = res_upload.json()["id"]

    headers = {"Authorization": f"Bearer {token}"}
    url = f"/v1/notebooks/{nb_id}/sources/{src_id}/file"

    # 1. No Range header -> 200 full file
    res_full = client.get(url, headers=headers)
    assert res_full.status_code == 200
    assert res_full.content == pdf_bytes
    assert res_full.headers["content-length"] == str(total_size)
    assert res_full.headers["content-type"] == "application/pdf"
    assert res_full.headers["content-disposition"] == "inline"
    assert res_full.headers["accept-ranges"] == "bytes"

    # 2. bytes=0-99 -> 206 first 100 bytes
    res_r1 = client.get(url, headers={**headers, "Range": "bytes=0-99"})
    assert res_r1.status_code == 206
    assert res_r1.content == pdf_bytes[:100]
    assert res_r1.headers["content-length"] == "100"
    assert res_r1.headers["content-range"] == f"bytes 0-99/{total_size}"
    assert res_r1.headers["accept-ranges"] == "bytes"
    assert res_r1.headers["content-disposition"] == "inline"

    # 3. bytes=0-0 -> 206 first byte
    res_r0 = client.get(url, headers={**headers, "Range": "bytes=0-0"})
    assert res_r0.status_code == 206
    assert res_r0.content == pdf_bytes[:1]
    assert res_r0.headers["content-length"] == "1"
    assert res_r0.headers["content-range"] == f"bytes 0-0/{total_size}"

    # 4. bytes=-50 -> 206 last 50 bytes
    res_suffix = client.get(url, headers={**headers, "Range": "bytes=-50"})
    assert res_suffix.status_code == 206
    expected_range = f"bytes {total_size-50}-{total_size-1}/{total_size}"
    assert res_suffix.headers["content-range"] == expected_range

    # 5. bytes=-99999999 (n > total_size) -> 206 whole file
    res_large_suffix = client.get(url, headers={**headers, "Range": "bytes=-99999999"})
    assert res_large_suffix.status_code == 206
    assert res_large_suffix.content == pdf_bytes
    assert res_large_suffix.headers["content-length"] == str(total_size)

    # 6. bytes=-0 -> 416
    res_zero_suffix = client.get(url, headers={**headers, "Range": "bytes=-0"})
    assert res_zero_suffix.status_code == 416
    assert res_zero_suffix.headers["content-range"] == f"bytes */{total_size}"
    assert res_zero_suffix.headers["accept-ranges"] == "bytes"

    # 7. bytes=99999999- (a >= total_size) -> 416
    res_unsat = client.get(url, headers={**headers, "Range": "bytes=99999999-"})
    assert res_unsat.status_code == 416
    assert res_unsat.headers["content-range"] == f"bytes */{total_size}"
    assert res_unsat.headers["accept-ranges"] == "bytes"

    # 8. Multiple ranges -> 200 full file
    res_multi = client.get(url, headers={**headers, "Range": "bytes=0-10,20-30"})
    assert res_multi.status_code == 200
    assert res_multi.content == pdf_bytes

    # 9. Malformed range -> 200 full file
    res_malformed = client.get(url, headers={**headers, "Range": "bytes=not-a-range"})
    assert res_malformed.status_code == 200
    assert res_malformed.content == pdf_bytes

    # 10. CORS check with Origin header
    res_cors = client.get(
        url,
        headers={**headers, "Range": "bytes=0-9", "Origin": "http://localhost:3000"},
    )
    assert res_cors.status_code == 206
    assert res_cors.headers.get("access-control-allow-origin") == "http://localhost:3000"
    exposed = res_cors.headers.get("access-control-expose-headers", "").lower()
    assert "accept-ranges" in exposed
    assert "content-range" in exposed
    assert "content-length" in exposed


def test_tx1_concurrency_ref_n(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"concur_user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    src_id1 = generate_source_id(nb_id)
    src_id2 = generate_source_id(nb_id)

    def run_tx(src_id: str, title: str):
        return create_source_transaction(
            nb=nb_id,
            source_id=src_id,
            title=title,
            filename=f"{title}.pdf",
            role="content",
            storage_path=f"notebooks/{nb_id}/sources/{src_id}/original.pdf",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(run_tx, src_id1, "DocA")
        f2 = executor.submit(run_tx, src_id2, "DocB")
        ref_n1 = f1.result()
        ref_n2 = f2.result()

    assert {ref_n1, ref_n2} == {1, 2}


def test_embedding_lazy_load_thread_safety(monkeypatch):
    """8 concurrent threads calling embedding functions load the model safely with 1 init."""
    import app.embeddings as emb_mod

    # Reset singletons
    emb_mod._model_instance = None
    emb_mod._tokenizer_instance = None

    orig_init = TextEmbedding.__init__
    init_counter = 0
    lock = threading.Lock()

    def counting_init(self, *args, **kwargs):
        nonlocal init_counter
        with lock:
            init_counter += 1
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(TextEmbedding, "__init__", counting_init)

    def task(i: int):
        q_emb = embed_query(f"What is the rank of matrix {i}?")
        p_emb = embed_passages([f"Passage content {i}"])[0]
        return len(q_emb), len(p_emb)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(task, i) for i in range(8)]
        results = [f.result() for f in futures]

    for q_len, p_len in results:
        assert q_len == 384
        assert p_len == 384

    assert init_counter == 1


def test_vector_search_smoke_test_on_real_chunks(user_tracker, notebook_tracker):
    """Vector smoke test running find_nearest on chunks from real PDF upload."""
    uid, token = create_emulator_user(email=f"vector_user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    # Ingest a PDF with distinct concepts
    pdf_bytes = create_pdf([
        "Bayes theorem describes the conditional probability of an event based on prior knowledge.",
        "A differential equation relates a function with its derivatives in calculus.",
    ])

    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("math_concepts.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res.status_code == 202
    assert res.json()["status"] == "ready"

    # Query embedding for "conditional probability"
    query_vec = embed_query("What is conditional probability in Bayes theorem?")
    assert len(query_vec) == 384

    # Run find_nearest against the Firestore emulator chunks collection
    db = get_db()
    chunks_coll = db.collection(chunks_collection_path(nb_id))
    vector_query = chunks_coll.find_nearest(
        vector_field="embedding",
        query_vector=Vector(query_vec),
        distance_measure=DistanceMeasure.COSINE,
        limit=2,
        distance_result_field="distance",
    )
    results = list(vector_query.get())
    assert len(results) == 2

    # The Bayes theorem chunk should be closest (lowest cosine distance)
    nearest_text = results[0].to_dict()["text"]
    assert "Bayes theorem" in nearest_text
    assert "distance" in results[0].to_dict()


def test_embeddings_instruction_and_tokenizer():
    # embed_query adds prefix
    query_emb1 = embed_query("Test query")
    query_emb2 = embed_query("Represent this sentence for searching relevant passages: Test query")
    assert query_emb1 == query_emb2

    # embed_passages adds none
    pass_emb = embed_passages(["Test query"])[0]
    # Passage embedding does not match query embedding because query embedding has prefix
    assert pass_emb != query_emb1

    # count_tokens does not truncate at 512
    long_text = "word " * 600
    tokens = count_tokens(long_text)
    assert tokens > 512


def test_sources_exact_operation_ids():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    paths = schema["paths"]
    assert paths["/v1/notebooks/{nb}/sources"]["post"]["operationId"] == "sources_create"
    assert paths["/v1/notebooks/{nb}/sources"]["get"]["operationId"] == "sources_list"
    assert paths["/v1/notebooks/{nb}/sources/{src}"]["get"]["operationId"] == "sources_get"
    assert (
        paths["/v1/notebooks/{nb}/sources/{src}/file"]["get"]["operationId"]
        == "sources_get_file"
    )


def test_split_oversized_word_retains_all_text():
    from app.ingestion.pdf import _split_oversized_word

    word = "ab." * 500 + "TAIL"
    pieces = _split_oversized_word(word)
    assert len(pieces) > 1
    for piece_text, piece_tokens in pieces:
        assert piece_tokens <= 400
        assert count_tokens(piece_text) <= 400
    joined = "".join(p[0] for p in pieces)
    assert joined == word
    assert "TAIL" in joined


def test_source_upload_failure_after_chunks_written_cleans_up(
    user_tracker, notebook_tracker, monkeypatch
):
    uid, token = create_emulator_user(email=f"fail_user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    # Monkeypatch finish_source_transaction to raise after chunks were already written to Firestore
    def mock_finish(*args, **kwargs):
        raise RuntimeError("Simulated failure in Tx2 after writing chunks")

    monkeypatch.setattr("app.api.sources.finish_source_transaction", mock_finish)

    pdf_bytes = create_pdf(["Some test content for fail cleanup."])
    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("fail.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res.status_code == 202
    src_data = res.json()
    assert src_data["status"] == "failed"
    assert src_data["stage"] == "save"
    assert src_data["error"] == "Processing failed at save."

    db = get_db()
    # Chunks written before Tx2 must have been deleted by fail_source_cleanup
    chunks = list(
        db.collection(chunks_collection_path(nb_id))
        .where(filter=FieldFilter("source_id", "==", src_data["id"]))
        .stream()
    )
    assert len(chunks) == 0

    nb_dict = db.document(notebook_path(nb_id)).get().to_dict()
    assert nb_dict["status"] == "empty"


def test_source_encrypted_pdf_rejected_cleanly(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email=f"enc_user_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 72), "Confidential encrypted notes.")
        enc_bytes = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="password123"
        )

    res = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("encrypted.pdf", BytesIO(enc_bytes), "application/pdf")},
    )
    assert res.status_code == 202
    src_data = res.json()
    assert src_data["status"] == "failed"
    assert src_data["stage"] == "extract"
    assert src_data["error"] == "Password-protected PDFs are not supported."

    db = get_db()
    chunks = list(db.collection(chunks_collection_path(nb_id)).stream())
    assert len(chunks) == 0

    nb_dict = db.document(notebook_path(nb_id)).get().to_dict()
    assert nb_dict["status"] == "empty"


def test_source_normal_pdf_upload_licence_attribution_youtube_url_null(
    user_tracker, notebook_tracker
):
    """Normal PDF upload: upload, list, and get return licence, attribution, youtube_url null."""
    uid, token = create_emulator_user(email=f"normal_pdf_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    pdf_bytes = create_pdf(["Normal PDF content without custom licence or video data."])
    headers = {"Authorization": f"Bearer {token}"}

    res_upload = client.post(
        f"/v1/notebooks/{nb_id}/sources",
        headers=headers,
        files={"file": ("normal.pdf", BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res_upload.status_code == 202
    upload_data = res_upload.json()
    assert upload_data["licence"] is None
    assert upload_data["attribution"] is None
    assert upload_data["youtube_url"] is None
    src_id = upload_data["id"]

    res_list = client.get(f"/v1/notebooks/{nb_id}/sources", headers=headers)
    assert res_list.status_code == 200
    list_items = res_list.json()["items"]
    assert len(list_items) == 1
    assert list_items[0]["id"] == src_id
    assert list_items[0]["licence"] is None
    assert list_items[0]["attribution"] is None
    assert list_items[0]["youtube_url"] is None

    res_get = client.get(f"/v1/notebooks/{nb_id}/sources/{src_id}", headers=headers)
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert get_data["id"] == src_id
    assert get_data["licence"] is None
    assert get_data["attribution"] is None
    assert get_data["youtube_url"] is None


def test_source_seeded_pdf_licence_and_attribution(user_tracker, notebook_tracker):
    """Seeded PDF with licence and attribution returns those exact strings; youtube_url null."""
    uid, token = create_emulator_user(email=f"seeded_pdf_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    db = get_db()
    src_id = generate_source_id(nb_id)
    licence_val = "CC BY-NC-SA 4.0"
    attribution_val = "MIT OpenCourseWare 6.041, Fall 2010"

    source_doc = {
        "ref_n": 1,
        "title": "6.041 Probability Notes",
        "kind": "pdf",
        "role": "content",
        "filename": "notes.pdf",
        "storage_path": f"notebooks/{nb_id}/sources/{src_id}/original.pdf",
        "viewer_path": f"notebooks/{nb_id}/sources/{src_id}/original.pdf",
        "youtube_id": None,
        "offset_s": None,
        "duration_s": None,
        "page_count": 5,
        "licence": licence_val,
        "attribution": attribution_val,
        "status": "ready",
        "stage": "done",
        "error": None,
        "created_at": datetime.now(UTC),
    }
    db.document(f"notebooks/{nb_id}/sources/{src_id}").set(source_doc)

    headers = {"Authorization": f"Bearer {token}"}

    res_list = client.get(f"/v1/notebooks/{nb_id}/sources", headers=headers)
    assert res_list.status_code == 200
    list_items = res_list.json()["items"]
    assert len(list_items) == 1
    assert list_items[0]["id"] == src_id
    assert list_items[0]["licence"] == licence_val
    assert list_items[0]["attribution"] == attribution_val
    assert list_items[0]["youtube_url"] is None

    res_get = client.get(f"/v1/notebooks/{nb_id}/sources/{src_id}", headers=headers)
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert get_data["id"] == src_id
    assert get_data["licence"] == licence_val
    assert get_data["attribution"] == attribution_val
    assert get_data["youtube_url"] is None


def test_source_seeded_video_youtube_url_variations(user_tracker, notebook_tracker):
    """Test youtube_url computation for seeded video sources and kind='pdf'."""
    from app.models.source import SourceOut

    uid, token = create_emulator_user(email=f"video_src_{uuid4().hex[:8]}@example.com")
    user_tracker.append(uid)
    nb_id = create_test_notebook(token)
    notebook_tracker.append(nb_id)

    db = get_db()
    headers = {"Authorization": f"Bearer {token}"}

    # Case 1: video, youtube_id "j9WZyLZCBzs", offset_s 0 -> "https://www.youtube.com/watch?v=j9WZyLZCBzs"
    src_v1 = generate_source_id(nb_id)
    doc_v1 = {
        "ref_n": 1,
        "title": "Lecture 1 Video",
        "kind": "video",
        "role": "content",
        "filename": "lec1.mp4",
        "storage_path": "",
        "viewer_path": "",
        "youtube_id": "j9WZyLZCBzs",
        "offset_s": 0,
        "duration_s": 3000.0,
        "licence": None,
        "attribution": None,
        "status": "ready",
        "stage": "done",
        "error": None,
        "created_at": datetime.now(UTC),
    }
    db.document(f"notebooks/{nb_id}/sources/{src_v1}").set(doc_v1)

    # Case 2: video, youtube_id "j9WZyLZCBzs", offset_s 12.7 -> "https://www.youtube.com/watch?v=j9WZyLZCBzs&t=12s"
    src_v2 = generate_source_id(nb_id)
    doc_v2 = {
        "ref_n": 2,
        "title": "Lecture 2 Video",
        "kind": "video",
        "role": "content",
        "filename": "lec2.mp4",
        "storage_path": "",
        "viewer_path": "",
        "youtube_id": "j9WZyLZCBzs",
        "offset_s": 12.7,
        "duration_s": 3000.0,
        "licence": None,
        "attribution": None,
        "status": "ready",
        "stage": "done",
        "error": None,
        "created_at": datetime.now(UTC),
    }
    db.document(f"notebooks/{nb_id}/sources/{src_v2}").set(doc_v2)

    # Case 3: video, youtube_id "j9WZyLZCBzs", offset_s 0.4 -> no &t= in youtube_url
    src_v3 = generate_source_id(nb_id)
    doc_v3 = {
        "ref_n": 3,
        "title": "Lecture 3 Video",
        "kind": "video",
        "role": "content",
        "filename": "lec3.mp4",
        "storage_path": "",
        "viewer_path": "",
        "youtube_id": "j9WZyLZCBzs",
        "offset_s": 0.4,
        "duration_s": 3000.0,
        "licence": None,
        "attribution": None,
        "status": "ready",
        "stage": "done",
        "error": None,
        "created_at": datetime.now(UTC),
    }
    db.document(f"notebooks/{nb_id}/sources/{src_v3}").set(doc_v3)

    # Case 4: seeded source with kind "pdf" but youtube_id set -> youtube_url null
    src_pdf = generate_source_id(nb_id)
    doc_pdf = {
        "ref_n": 4,
        "title": "PDF with youtube_id",
        "kind": "pdf",
        "role": "content",
        "filename": "doc.pdf",
        "storage_path": "",
        "viewer_path": "",
        "youtube_id": "j9WZyLZCBzs",
        "offset_s": 12.7,
        "duration_s": None,
        "licence": None,
        "attribution": None,
        "status": "ready",
        "stage": "done",
        "error": None,
        "created_at": datetime.now(UTC),
    }
    db.document(f"notebooks/{nb_id}/sources/{src_pdf}").set(doc_pdf)

    # Verify through API (GET /v1/notebooks/{nb}/sources/{src})
    res_g1 = client.get(f"/v1/notebooks/{nb_id}/sources/{src_v1}", headers=headers)
    assert res_g1.status_code == 200
    assert res_g1.json()["youtube_url"] == "https://www.youtube.com/watch?v=j9WZyLZCBzs"

    res_g2 = client.get(f"/v1/notebooks/{nb_id}/sources/{src_v2}", headers=headers)
    assert res_g2.status_code == 200
    assert res_g2.json()["youtube_url"] == "https://www.youtube.com/watch?v=j9WZyLZCBzs&t=12s"

    res_g3 = client.get(f"/v1/notebooks/{nb_id}/sources/{src_v3}", headers=headers)
    assert res_g3.status_code == 200
    assert res_g3.json()["youtube_url"] == "https://www.youtube.com/watch?v=j9WZyLZCBzs"

    res_g4 = client.get(f"/v1/notebooks/{nb_id}/sources/{src_pdf}", headers=headers)
    assert res_g4.status_code == 200
    assert res_g4.json()["youtube_url"] is None

    # Verify through API (GET /v1/notebooks/{nb}/sources)
    res_list = client.get(f"/v1/notebooks/{nb_id}/sources", headers=headers)
    assert res_list.status_code == 200
    items = {item["id"]: item for item in res_list.json()["items"]}
    assert items[src_v1]["youtube_url"] == "https://www.youtube.com/watch?v=j9WZyLZCBzs"
    assert items[src_v2]["youtube_url"] == "https://www.youtube.com/watch?v=j9WZyLZCBzs&t=12s"
    assert items[src_v3]["youtube_url"] == "https://www.youtube.com/watch?v=j9WZyLZCBzs"
    assert items[src_pdf]["youtube_url"] is None

    # Verify unit-level SourceOut.from_stored
    assert SourceOut.from_stored(doc_v1).youtube_url == "https://www.youtube.com/watch?v=j9WZyLZCBzs"
    assert SourceOut.from_stored(doc_v2).youtube_url == "https://www.youtube.com/watch?v=j9WZyLZCBzs&t=12s"
    assert SourceOut.from_stored(doc_v3).youtube_url == "https://www.youtube.com/watch?v=j9WZyLZCBzs"
    assert SourceOut.from_stored(doc_pdf).youtube_url is None

    # Verify URL is never stored in Firestore documents
    for s_id in (src_v1, src_v2, src_v3, src_pdf):
        stored = db.document(f"notebooks/{nb_id}/sources/{s_id}").get().to_dict()
        assert "youtube_url" not in stored


def test_openapi_schema_source_fields():
    """OpenAPI schema lists licence, attribution and youtube_url allowing string or null."""
    res = client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()
    schemas = schema["components"]["schemas"]
    source_schema = schemas.get("SourceOut") or schemas.get("Source")
    assert source_schema is not None, "Source schema not found in components"
    props = source_schema["properties"]

    for field in ("licence", "attribution", "youtube_url"):
        assert field in props, f"{field} missing from OpenAPI schema properties"
        prop = props[field]
        types = set()
        if "anyOf" in prop:
            for item in prop["anyOf"]:
                if "type" in item:
                    types.add(item["type"])
        if "type" in prop:
            types.add(prop["type"])
        if prop.get("nullable"):
            types.add("null")

        assert "string" in types, f"{field} does not allow string: {prop}"
        assert "null" in types, f"{field} does not allow null: {prop}"
