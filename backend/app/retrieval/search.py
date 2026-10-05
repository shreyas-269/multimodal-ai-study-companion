from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
from pydantic import BaseModel

from app.db import chunks_collection_path, get_db
from app.embeddings import embed_query
from app.models.citation import Location
from app.models.notebook import Notebook


class RetrievedChunk(BaseModel):
    chunk_id: str
    source_id: str
    text: str
    loc: Location
    score: float


def search(notebook: Notebook, question: str) -> list[RetrievedChunk]:
    ready_source_ids = {s.source_id for s in notebook.sources_summary if s.status == "ready"}
    if not ready_source_ids:
        return []

    query_vec = embed_query(question)
    db = get_db()
    chunks_coll = db.collection(chunks_collection_path(notebook.id))

    vector_query = chunks_coll.find_nearest(
        vector_field="embedding",
        query_vector=Vector(query_vec),
        distance_measure=DistanceMeasure.COSINE,
        limit=40,
        distance_result_field="distance",
    )

    results: list[RetrievedChunk] = []
    for doc in vector_query.get():
        data = doc.to_dict() or {}
        source_id = data.get("source_id", "")
        if source_id not in ready_source_ids:
            continue

        distance = float(data.get("distance", 0.0))
        score = 1.0 - distance
        loc_data = data.get("loc") or {}
        loc = Location.model_validate(loc_data)

        results.append(RetrievedChunk(
            chunk_id=doc.id,
            source_id=source_id,
            text=data.get("text", ""),
            loc=loc,
            score=score,
        ))

        if len(results) == 10:
            break

    return results
