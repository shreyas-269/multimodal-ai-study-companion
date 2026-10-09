import re
from typing import Any

import pymupdf

from app.embeddings import count_tokens, get_tokenizer
from app.ingestion.labels import compute_page_labels, is_licence_page
from app.ingestion.models import ParsedPdf
from app.ingestion.slides import parse_slides_on_page

MAX_CHUNK_TOKENS = 400


def _split_oversized_word(word: str) -> list[tuple[str, int]]:
    """Last-resort split for an oversized word using tokenizer encoding offsets."""
    tokenizer = get_tokenizer()
    encoding = tokenizer.encode(word, add_special_tokens=False)
    pieces = []
    offsets = encoding.offsets
    total_tokens = len(encoding.ids)
    step = 398

    for i in range(0, total_tokens, step):
        slice_offsets = offsets[i : i + step]
        if not slice_offsets:
            continue
        start_char = slice_offsets[0][0]
        end_char = slice_offsets[-1][1]
        word_slice = word[start_char:end_char]
        if word_slice:
            pieces.append((word_slice, count_tokens(word_slice)))
    return pieces


def _split_block_into_pieces(block_text: str) -> list[tuple[str, int]]:
    """Split block text into sub-pieces each guaranteed to be <= MAX_CHUNK_TOKENS."""
    block_tokens = count_tokens(block_text)
    if block_tokens <= MAX_CHUNK_TOKENS:
        return [(block_text, block_tokens)]

    # Split into sentences
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", block_text) if s.strip()]
    pieces = []

    for sentence in sentences:
        s_tokens = count_tokens(sentence)
        if s_tokens <= MAX_CHUNK_TOKENS:
            pieces.append((sentence, s_tokens))
            continue

        # Split sentence into words
        words = sentence.split()
        current_words: list[str] = []
        current_tokens = 0

        for word in words:
            w_tokens = count_tokens(word)
            if w_tokens > MAX_CHUNK_TOKENS:
                if current_words:
                    joined = " ".join(current_words)
                    pieces.append((joined, current_tokens))
                    current_words = []
                    current_tokens = 0
                pieces.extend(_split_oversized_word(word))
            else:
                candidate = " ".join(current_words + [word])
                cand_tokens = count_tokens(candidate)
                if cand_tokens <= MAX_CHUNK_TOKENS:
                    current_words.append(word)
                    current_tokens = cand_tokens
                else:
                    if current_words:
                        pieces.append((" ".join(current_words), current_tokens))
                    current_words = [word]
                    current_tokens = w_tokens

        if current_words:
            pieces.append((" ".join(current_words), current_tokens))

    return pieces


def _pack_pieces(
    pieces: list[tuple[str, int]],
    loc: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pack pieces into chunks <= MAX_CHUNK_TOKENS."""
    if not pieces:
        return []

    chunks: list[dict[str, Any]] = []
    current_pieces: list[tuple[str, int]] = []
    current_tokens = 0

    for piece_text, piece_tokens in pieces:
        if not current_pieces:
            current_pieces = [(piece_text, piece_tokens)]
            current_tokens = piece_tokens
            continue

        candidate = "\n\n".join([p[0] for p in current_pieces] + [piece_text])
        cand_tokens = count_tokens(candidate)
        if cand_tokens <= MAX_CHUNK_TOKENS:
            current_pieces.append((piece_text, piece_tokens))
            current_tokens = cand_tokens
        else:
            chunk_text = "\n\n".join(p[0] for p in current_pieces)
            chunks.append(
                {
                    "text": chunk_text,
                    "token_count": current_tokens,
                    "loc": dict(loc),
                }
            )
            current_pieces = [(piece_text, piece_tokens)]
            current_tokens = piece_tokens

    if current_pieces:
        chunk_text = "\n\n".join(p[0] for p in current_pieces)
        chunks.append(
            {
                "text": chunk_text,
                "token_count": current_tokens,
                "loc": dict(loc),
            }
        )

    return chunks


def parse_pdf(
    data: bytes,
    *,
    source_id: str,
    slide_grid: str | None = None,
) -> ParsedPdf:
    """Pure parse function extracting labels, excluding licence pages, and chunking text."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if doc.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")

        page_count = len(doc)
        grid_rc: tuple[int, int] | None = None
        if slide_grid is not None:
            m = re.match(r"^([1-4])x([1-4])$", slide_grid.strip())
            if not m:
                msg = f"Invalid slide_grid: {slide_grid}. Expected format RxC (e.g. 2x2)."
                raise ValueError(msg)
            grid_rc = (int(m.group(1)), int(m.group(2)))

        # 1. Fetch get_text("dict") once per page and cache
        page_dicts: list[dict[str, Any]] = []
        for page_idx in range(page_count):
            page_dicts.append(doc[page_idx].get_text("dict"))

        # 2. Identify licence pages (1-based page numbers)
        licence_pages: list[int] = []
        for page_idx in range(page_count):
            p_num = page_idx + 1
            if is_licence_page(doc[page_idx], page_dicts[page_idx]):
                licence_pages.append(p_num)

        licence_set = set(licence_pages)

        # 3. Compute page labels
        page_labels = compute_page_labels(
            doc=doc,
            page_dicts=page_dicts,
            licence_pages=licence_set,
            has_slide_grid=(grid_rc is not None),
        )
        assert len(page_labels) == page_count, "len(page_labels) must match page_count"

        # 4. Extract and chunk text
        raw_chunks: list[dict[str, Any]] = []
        current_slide_num = 1

        for page_idx in range(page_count):
            physical_page = page_idx + 1
            if physical_page in licence_set:
                continue

            page = doc[page_idx]
            page_dict = page_dicts[page_idx]

            if grid_rc is not None:
                # Slide grid mode
                r, c = grid_rc
                slides, current_slide_num = parse_slides_on_page(
                    page=page,
                    page_dict=page_dict,
                    r=r,
                    c=c,
                    start_slide=current_slide_num,
                )
                for slide_num, cell_bbox, lines in slides:
                    slide_pieces: list[tuple[str, int]] = []
                    # Process lines into pieces
                    slide_text = "\n".join(lines).strip()
                    if not slide_text:
                        continue
                    slide_pieces = _split_block_into_pieces(slide_text)
                    loc = {
                        "source_id": source_id,
                        "page": physical_page,
                        "page_label": None,
                        "slide": slide_num,
                        "bbox": cell_bbox,
                    }
                    raw_chunks.extend(_pack_pieces(slide_pieces, loc))
            else:
                # Standard PDF mode
                # Extract blocks in native reading order from page_dict
                text_blocks: list[str] = []
                for b in page_dict.get("blocks", []):
                    if b.get("type", 0) == 0 and "lines" in b:
                        block_lines: list[str] = []
                        for line in b["lines"]:
                            spans_text = "".join(s.get("text", "") for s in line.get("spans", []))
                            if spans_text.strip():
                                block_lines.append(spans_text)
                        joined_block = "\n".join(block_lines).strip()
                        if joined_block:
                            text_blocks.append(joined_block)

                if not text_blocks:
                    continue

                page_pieces: list[tuple[str, int]] = []
                for block_text in text_blocks:
                    page_pieces.extend(_split_block_into_pieces(block_text))

                loc = {
                    "source_id": source_id,
                    "page": physical_page,
                    "page_label": page_labels[page_idx],
                    "slide": None,
                    "bbox": None,
                }
                raw_chunks.extend(_pack_pieces(page_pieces, loc))

        if not raw_chunks:
            raise ValueError("This PDF has no extractable text (it may be scanned).")

        # Assign deterministic sequential IDs {source_id}-{seq:05d} from 00000
        final_chunks: list[dict[str, Any]] = []
        for seq_idx, chunk in enumerate(raw_chunks):
            chunk["id"] = f"{source_id}-{seq_idx:05d}"
            final_chunks.append(chunk)

        return ParsedPdf(
            page_count=page_count,
            page_labels=page_labels,
            licence_pages=licence_pages,
            chunks=final_chunks,
        )
