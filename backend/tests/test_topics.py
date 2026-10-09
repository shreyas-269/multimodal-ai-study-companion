import uuid

import pytest
from fastapi.testclient import TestClient
from firebase_admin import firestore

from app.chat.citations import build_citation
from app.db import get_db, notebook_path
from app.db.notebooks import create_notebook
from app.db.topics import write_topics_batch
from app.ingestion.syllabus import parse_syllabus
from app.ingestion.topics import (
    VIDEO_WINDOW_S,
    TopicRange,
    group_topic_locations,
    location_key,
    parse_topics_column,
    resolve_chunk_topic,
)
from app.main import app
from app.models.citation import Location
from tests.conftest import create_emulator_user


def test_parse_syllabus_synthetic():
    text = """# Probability
1. Intro to probability. Prerequisites: none (elementary logic)
2. Conditional probability. Prerequisites: 1
3. Independence. Prerequisites: 2
4. Combinatorics. Prerequisites: 1
5. Random variables. Prerequisites: 1, 4
6. Joint distributions. Prerequisites: 2, 5
"""
    topics = parse_syllabus(text)
    assert len(topics) == 6
    assert [t.id for t in topics] == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert [t.order for t in topics] == [1, 2, 3, 4, 5, 6]
    assert topics[0].prerequisite_ids == []
    assert topics[1].prerequisite_ids == ["t1"]
    assert topics[2].prerequisite_ids == ["t2"]
    assert topics[3].prerequisite_ids == ["t1"]
    assert topics[4].prerequisite_ids == ["t1", "t4"]
    assert topics[5].prerequisite_ids == ["t2", "t5"]
    assert all(not t.is_other for t in topics)


def test_topics_column_parser_single_topic():
    res = parse_topics_column("t3")
    assert res == "t3"

    res_empty = parse_topics_column("")
    assert res_empty == ""


def test_topics_column_parser_ranges():
    spec = (
        "2-24=t1;25-69=t4;70-74=t2;75=t2|t3;76-82=t3;83-85=t2;86-99=t2|t3;"
        "100-101=t5|t6;102-121=t6;122-136=t5;137=t5|t6;138-139=t6;140-154=t5|t6;155-165=t5"
    )
    ranges = parse_topics_column(spec)
    assert isinstance(ranges, list)
    assert len(ranges) == 14
    assert ranges[0] == TopicRange(2, 24, ["t1"])
    assert ranges[1] == TopicRange(25, 69, ["t4"])
    assert ranges[2] == TopicRange(70, 74, ["t2"])
    assert ranges[3] == TopicRange(75, 75, ["t2", "t3"])
    assert ranges[4] == TopicRange(76, 82, ["t3"])
    assert ranges[5] == TopicRange(83, 85, ["t2"])
    assert ranges[6] == TopicRange(86, 99, ["t2", "t3"])
    assert ranges[7] == TopicRange(100, 101, ["t5", "t6"])
    assert ranges[8] == TopicRange(102, 121, ["t6"])
    assert ranges[9] == TopicRange(122, 136, ["t5"])
    assert ranges[10] == TopicRange(137, 137, ["t5", "t6"])
    assert ranges[11] == TopicRange(138, 139, ["t6"])
    assert ranges[12] == TopicRange(140, 154, ["t5", "t6"])
    assert ranges[13] == TopicRange(155, 165, ["t5"])


def test_topics_column_parser_validation_errors():
    with pytest.raises(ValueError, match="Invalid range start > end"):
        parse_topics_column("24-2=t1")

    with pytest.raises(ValueError, match="Overlapping range detected"):
        parse_topics_column("2-24=t1;20-50=t2")

    with pytest.raises(ValueError, match="Invalid topic range specification"):
        parse_topics_column("invalid_spec=not_a_range")


def test_tie_breaking_deterministic():
    mapping = [TopicRange(1, 10, ["t2", "t3"])]
    query_embs = {
        "t2": [1.0, 0.0, 0.0],
        "t3": [0.0, 1.0, 0.0],
    }

    emb_t2 = [0.9, 0.1, 0.0]
    assert resolve_chunk_topic(5, emb_t2, mapping, query_embs) == "t2"

    emb_t3 = [0.1, 0.9, 0.0]
    assert resolve_chunk_topic(5, emb_t3, mapping, query_embs) == "t3"

    # Exact tie: equal cosine similarity -> alphabetical tie-break chooses "t2"
    emb_tie = [0.5, 0.5, 0.0]
    assert resolve_chunk_topic(5, emb_tie, mapping, query_embs) == "t2"


def test_location_key_pdf_and_video():
    loc_pdf = {"source_id": "src_l01_slides", "page": 2, "slide": 3}
    assert location_key(loc_pdf) == ("src_l01_slides", 2, 3)

    assert VIDEO_WINDOW_S == 300
    loc_video1 = {"source_id": "src_l01_video", "t_start_s": 0.0}
    loc_video2 = {"source_id": "src_l01_video", "t_start_s": 299.9}
    loc_video3 = {"source_id": "src_l01_video", "t_start_s": 300.0}
    loc_video4 = {"source_id": "src_l01_video", "t_start_s": 650.0}
    assert location_key(loc_video1) == ("src_l01_video", 0)
    assert location_key(loc_video2) == ("src_l01_video", 0)
    assert location_key(loc_video3) == ("src_l01_video", 1)
    assert location_key(loc_video4) == ("src_l01_video", 2)


def test_location_grouping_and_order():
    chunks_with_src = [
        # Source 1 (ref_n=1): p1 slide 1
        (
            {
                "id": "src_1-00002",
                "source_id": "src_1",
                "loc": {"source_id": "src_1", "page": 1, "slide": 1},
            },
            1,
        ),
        (
            {
                "id": "src_1-00001",
                "source_id": "src_1",
                "loc": {"source_id": "src_1", "page": 1, "slide": 1},
            },
            1,
        ),
        # Source 2 (ref_n=2): p5
        (
            {
                "id": "src_2-00004",
                "source_id": "src_2",
                "loc": {"source_id": "src_2", "page": 5, "slide": None},
            },
            2,
        ),
        (
            {
                "id": "src_2-00001",
                "source_id": "src_2",
                "loc": {"source_id": "src_2", "page": 5, "slide": None},
            },
            2,
        ),
        # Source 1 (ref_n=1): p2 slide 3
        (
            {
                "id": "src_1-00003",
                "source_id": "src_1",
                "loc": {"source_id": "src_1", "page": 2, "slide": 3},
            },
            1,
        ),
        # Source 3 (ref_n=3): video window 0
        (
            {
                "id": "src_3-00005",
                "source_id": "src_3",
                "loc": {"source_id": "src_3", "t_start_s": 50.0},
            },
            3,
        ),
    ]

    grouped = group_topic_locations(chunks_with_src)
    assert len(grouped) == 4

    # 1. src_1, page 1, slide 1 -> chunk src_1-00001 (lowest seq)
    assert grouped[0]["chunk_id"] == "src_1-00001"
    assert grouped[0]["loc"]["page"] == 1
    assert grouped[0]["loc"]["slide"] == 1

    # 2. src_1, page 2, slide 3 -> chunk src_1-00003
    assert grouped[1]["chunk_id"] == "src_1-00003"
    assert grouped[1]["loc"]["page"] == 2
    assert grouped[1]["loc"]["slide"] == 3

    # 3. src_2, page 5 -> chunk src_2-00001 (lowest seq)
    assert grouped[2]["chunk_id"] == "src_2-00001"
    assert grouped[2]["loc"]["page"] == 5

    # 4. src_3, video window 0
    assert grouped[3]["chunk_id"] == "src_3-00005"
    assert grouped[3]["loc"]["t_start_s"] == 50.0


def test_operation_ids_pinned():
    openapi = app.openapi()
    paths = openapi["paths"]
    assert paths["/v1/notebooks/{nb}/topics"]["get"]["operationId"] == "topics_list"
    assert (
        paths["/v1/notebooks/{nb}/topics/{t}/sources"]["get"]["operationId"]
        == "topics_list_sources"
    )


def test_topics_endpoint_access_and_empty_list(user_tracker, notebook_tracker):
    u1, token1 = create_emulator_user(email="owner_t@test.com")
    u2, token2 = create_emulator_user(email="other_t@test.com")
    user_tracker.extend([u1, u2])

    client1 = TestClient(app, headers={"Authorization": f"Bearer {token1}"})
    client2 = TestClient(app, headers={"Authorization": f"Bearer {token2}"})

    res = client1.post("/v1/notebooks", json={"name": "Owner NB"})
    assert res.status_code == 201
    nb_id = res.json()["id"]
    notebook_tracker.append(nb_id)

    res_topics1 = client1.get(f"/v1/notebooks/{nb_id}/topics")
    assert res_topics1.status_code == 200
    assert res_topics1.json() == {"items": [], "next_cursor": None}

    res_topics2 = client2.get(f"/v1/notebooks/{nb_id}/topics")
    assert res_topics2.status_code == 404


def test_topics_endpoint_and_sources(user_tracker, notebook_tracker):
    uid, token = create_emulator_user(email="topics_src@test.com")
    user_tracker.append(uid)
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})

    nb_data = create_notebook(name="Topics Test", owner_uid=uid)
    nb_id = nb_data["id"]
    notebook_tracker.append(nb_id)

    db = get_db()
    src_id = "src_demo"
    src_unready_id = "src_unready"

    # Set notebook sources_summary with ready source and processing source
    db.document(f"notebooks/{nb_id}").update(
        {
            "sources_summary": [
                {
                    "source_id": src_id,
                    "ref_n": 1,
                    "title": "Demo Textbook",
                    "kind": "pdf",
                    "status": "ready",
                },
                {
                    "source_id": src_unready_id,
                    "ref_n": 2,
                    "title": "Unready Source",
                    "kind": "pdf",
                    "status": "processing",
                },
            ]
        }
    )

    sample_loc = Location(source_id=src_id, page=5, page_label="5", slide=None).model_dump()
    unready_loc = Location(
        source_id=src_unready_id, page=2, page_label="2", slide=None
    ).model_dump()

    topics = [
        {
            "id": "t1",
            "name": "Topic 1",
            "order": 1,
            "summary": "Summary 1",
            "prerequisite_ids": [],
            "is_other": False,
            "locations": [
                {"chunk_id": f"{src_id}-00001", "loc": sample_loc},
                {"chunk_id": f"{src_unready_id}-00001", "loc": unready_loc},
            ],
            "location_count": 2,
        },
        {
            "id": "t2",
            "name": "Topic 2",
            "order": 2,
            "summary": "Summary 2",
            "prerequisite_ids": ["t1"],
            "is_other": False,
            "locations": [],
            "location_count": 0,
        },
        {
            "id": "other",
            "name": "Other material",
            "order": 7,
            "summary": "Other",
            "prerequisite_ids": [],
            "is_other": True,
            "locations": [],
            "location_count": 0,
        },
    ]
    write_topics_batch(nb_id, topics)

    # GET /topics
    res = client.get(f"/v1/notebooks/{nb_id}/topics")
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    # "other" has 0 locations, so only t1 and t2 appear
    assert len(items) == 2
    assert items[0]["id"] == "t1"
    assert items[0]["location_count"] == 2
    assert items[1]["id"] == "t2"
    assert items[1]["location_count"] == 0

    # GET /topics/t1/sources - unready source is filtered out
    res_src = client.get(f"/v1/notebooks/{nb_id}/topics/t1/sources")
    assert res_src.status_code == 200
    src_data = res_src.json()
    assert len(src_data["items"]) == 1
    citation = src_data["items"][0]
    expected_citation = build_citation(
        chunk_id=f"{src_id}-00001",
        loc=Location.model_validate(sample_loc),
        title="Demo Textbook",
    )
    assert citation["chunk_id"] == expected_citation.chunk_id
    assert citation["label"] == expected_citation.label
    assert citation["open"] == expected_citation.open.model_dump()

    # Strict regex rejection: t1-t6 and other only
    assert client.get(f"/v1/notebooks/{nb_id}/topics/t0/sources").status_code == 404
    assert client.get(f"/v1/notebooks/{nb_id}/topics/t7/sources").status_code == 404
    assert client.get(f"/v1/notebooks/{nb_id}/topics/invalid!!/sources").status_code == 404
    assert client.get(f"/v1/notebooks/{nb_id}/topics/nonexistent/sources").status_code == 404


def test_topics_sources_endpoint_video_youtube_citation(user_tracker, notebook_tracker):
    """Verify topic Sources endpoint returns YouTube citation for video locations."""
    uid, token = create_emulator_user(email="topics_vid@test.com")
    user_tracker.append(uid)
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    nb_id = f"nb_test_topics_vid_{uuid.uuid4().hex[:8]}"
    notebook_tracker.append(nb_id)
    db = get_db()

    vid_src_id = f"src_vid_{uuid.uuid4().hex[:6]}"
    db.document(f"notebooks/{nb_id}/sources/{vid_src_id}").set({
        "ref_n": 1,
        "title": "Lecture 2 Video",
        "kind": "video",
        "role": "content",
        "filename": "L02.mp4",
        "storage_path": "",
        "viewer_path": None,
        "youtube_id": "yt_test_789",
        "offset_s": 0.0,
        "duration_s": 3000.0,
        "status": "ready",
        "ingest_version": 1,
        "created_at": firestore.SERVER_TIMESTAMP,
    })

    db.document(notebook_path(nb_id)).set({
        "name": "Topics Video Test NB",
        "owner_uid": uid,
        "is_demo": False,
        "status": "ready",
        "sources_summary": [
            {
                "source_id": vid_src_id,
                "ref_n": 1,
                "title": "Lecture 2 Video",
                "kind": "video",
                "status": "ready",
            }
        ],
        "counts": {"chunks": 1},
        "created_at": firestore.SERVER_TIMESTAMP,
    })

    loc = {
        "source_id": vid_src_id,
        "t_start_s": 250.0,
        "t_end_s": 300.0,
    }
    topics = [
        {
            "id": "t2",
            "name": "Conditioning",
            "order": 2,
            "summary": "Conditioning",
            "prerequisite_ids": [],
            "is_other": False,
            "locations": [{"chunk_id": f"{vid_src_id}-00000", "loc": loc}],
            "location_count": 1,
        }
    ]
    write_topics_batch(nb_id, topics)

    try:
        res = client.get(f"/v1/notebooks/{nb_id}/topics/t2/sources")
        assert res.status_code == 200
        items = res.json()["items"]
        assert len(items) == 1
        cit = items[0]
        assert cit["label"] == "Lecture 2 Video, 4:10"
        assert cit["open"]["kind"] == "youtube"
        assert cit["open"]["url"] == "https://www.youtube.com/watch?v=yt_test_789&t=250s"
    finally:
        try:
            db.recursive_delete(db.document(notebook_path(nb_id)))
        except Exception:
            pass
