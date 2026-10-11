from dataclasses import dataclass
from typing import Any

from app.chat.citations import format_citation_time
from app.db.notebooks import get_notebook_snapshot
from app.db.sources import get_chunks_by_ids
from app.db.topics import get_topic_snapshot

NUMERICAL_FIRST_SOURCES: tuple[str, ...] = (
    "src_r01_sol",
    "src_r02_sol",
    "src_r03_sol",
    "src_r04_sol",
    "src_r05_sol",
    "src_r06_sol",
    "src_textbook",
)


@dataclass(frozen=True)
class SelectedPassage:
    chunk_id: str
    source_id: str
    ref_n: int
    title: str
    loc: dict[str, Any]
    text: str
    token_count: int
    passage_num: int


def is_numerical_first(source_id: str) -> bool:
    """Return True if source is recitation solutions (*_sol) or textbook."""
    return source_id in NUMERICAL_FIRST_SOURCES or source_id.endswith("_sol")


def format_passage_location(p: SelectedPassage) -> str:
    """Return human-readable location label (e.g. 'at 1:30' or 'page 5')."""
    loc = p.loc
    t_start_s = loc.get("t_start_s")
    if t_start_s is not None:
        return f"at {format_citation_time(t_start_s)}"
    page_label = loc.get("page_label")
    page = loc.get("page")
    if page_label:
        page_display = str(page_label)
    elif page is not None:
        page_display = str(page)
    else:
        page_display = "unknown"
    label = f"page {page_display}"
    slide = loc.get("slide")
    if slide is not None:
        label += f" (slide {slide})"
    return label


def select_topic_passages(
    nb: str,
    topic_id: str,
    qtype: str,
    max_passages: int = 14,
    max_tokens: int = 5000,
) -> list[SelectedPassage]:
    """Deterministically select up to 14 passages and up to 5,000 tokens for a topic."""
    topic_snap = get_topic_snapshot(nb, topic_id)
    if not topic_snap.exists:
        return []

    topic_data = topic_snap.to_dict() or {}
    locations = topic_data.get("locations", [])
    if not locations:
        return []

    nb_snap = get_notebook_snapshot(nb)
    nb_data = nb_snap.to_dict() or {} if nb_snap.exists else {}
    sources_summary = nb_data.get("sources_summary", [])
    src_meta: dict[str, dict[str, Any]] = {
        s.get("source_id"): {
            "ref_n": s.get("ref_n", 999),
            "title": s.get("title", s.get("source_id")),
            "status": s.get("status"),
        }
        for s in sources_summary
        if s.get("status") == "ready"
    }

    chunk_ids = [loc_item["chunk_id"] for loc_item in locations if "chunk_id" in loc_item]
    chunks_dict = get_chunks_by_ids(nb, chunk_ids, field_paths=("text", "token_count"))

    # Group candidate chunks by source_id, preserving deterministic order
    by_source: dict[str, list[dict[str, Any]]] = {}
    for loc_item in locations:
        cid = loc_item.get("chunk_id", "")
        raw_loc = loc_item.get("loc") or {}
        sid = raw_loc.get("source_id", "")
        if sid not in src_meta or cid not in chunks_dict:
            continue
        cdata = chunks_dict[cid]
        text = cdata.get("text", "")
        token_count = cdata.get("token_count", len(text.split()))

        item = {
            "chunk_id": cid,
            "source_id": sid,
            "ref_n": src_meta[sid]["ref_n"],
            "title": src_meta[sid]["title"],
            "loc": raw_loc,
            "text": text,
            "token_count": token_count,
        }
        by_source.setdefault(sid, []).append(item)

    chosen: list[dict[str, Any]] = []
    total_tokens = 0

    def add_from_source_groups(source_ids: list[str]) -> None:
        nonlocal total_tokens
        cursors = {sid: 0 for sid in source_ids}
        while True:
            added_any = False
            for sid in source_ids:
                if len(chosen) >= max_passages:
                    return
                src_chunks = by_source.get(sid, [])
                idx = cursors[sid]
                if idx < len(src_chunks):
                    candidate = src_chunks[idx]
                    cursors[sid] += 1
                    t_count = candidate["token_count"]
                    if total_tokens + t_count <= max_tokens:
                        chosen.append(candidate)
                        total_tokens += t_count
                        added_any = True
                    else:
                        # Cannot fit this passage within budget
                        continue
            if not added_any:
                break

    if qtype == "mcq":
        # Round-robin across all topic sources ordered by ref_n
        sorted_sources = sorted(by_source.keys(), key=lambda s: src_meta[s]["ref_n"])
        add_from_source_groups(sorted_sources)
    elif qtype == "numerical":
        # Recitation solutions and textbook first, then the rest
        sorted_sources = sorted(by_source.keys(), key=lambda s: src_meta[s]["ref_n"])
        priority_sources = [s for s in sorted_sources if is_numerical_first(s)]
        remaining_sources = [s for s in sorted_sources if not is_numerical_first(s)]

        add_from_source_groups(priority_sources)
        if len(chosen) < max_passages and total_tokens < max_tokens:
            add_from_source_groups(remaining_sources)
    else:
        sorted_sources = sorted(by_source.keys(), key=lambda s: src_meta[s]["ref_n"])
        add_from_source_groups(sorted_sources)

    return [
        SelectedPassage(
            chunk_id=item["chunk_id"],
            source_id=item["source_id"],
            ref_n=item["ref_n"],
            title=item["title"],
            loc=item["loc"],
            text=item["text"],
            token_count=item["token_count"],
            passage_num=idx + 1,
        )
        for idx, item in enumerate(chosen)
    ]


def format_passages_block(passages: list[SelectedPassage]) -> str:
    """Format passages into <<<SOURCE n>>> data blocks with <<<END SOURCE n>>>."""
    blocks = []
    for p in passages:
        loc = p.loc
        t_start_s = loc.get("t_start_s")
        if t_start_s is not None:
            time_str = format_citation_time(t_start_s)
            header = f"Source: {p.title}, at {time_str}"
        else:
            page = loc.get("page")
            page_label = loc.get("page_label")
            if page_label:
                page_display = page_label
            elif page is not None:
                page_display = str(page)
            else:
                page_display = "unknown"
            header = f"Source: {p.title}\nPage: {page_display}"
            slide = loc.get("slide")
            if slide is not None:
                header += f" (slide {slide})"

        blocks.append(
            f"<<<SOURCE {p.passage_num}>>>\n"
            f"{header}\n"
            "Content:\n"
            f"{p.text}\n"
            f"<<<END SOURCE {p.passage_num}>>>"
        )
    return "\n\n".join(blocks)
