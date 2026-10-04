import re
from typing import Any

import pymupdf

from app.embeddings import count_tokens, get_tokenizer

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


def extract_and_chunk_pdf(
    pdf_bytes: bytes,
    source_id: str,
) -> tuple[list[dict[str, Any]], int]:
    """Extract and chunk text from PDF bytes in PyMuPDF native order."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")

        page_count = len(doc)
        chunks: list[dict[str, Any]] = []
        total_extracted_text = ""

        for page_idx in range(page_count):
            page = doc[page_idx]
            physical_page = page_idx + 1

            # Extract blocks in PyMuPDF's native order (block_type 0 = text)
            raw_blocks = page.get_text("blocks")
            text_blocks = [
                b[4].strip()
                for b in raw_blocks
                if len(b) >= 7 and b[6] == 0 and b[4].strip()
            ]

            if not text_blocks:
                continue

            # Prepare pieces each guaranteed to be <= MAX_CHUNK_TOKENS
            page_pieces: list[tuple[str, int]] = []
            for block_text in text_blocks:
                total_extracted_text += block_text + "\n"
                page_pieces.extend(_split_block_into_pieces(block_text))

            # Pack pieces into chunks on this page
            current_chunk_pieces: list[tuple[str, int]] = []
            current_chunk_tokens = 0

            for piece_text, piece_tokens in page_pieces:
                if not current_chunk_pieces:
                    current_chunk_pieces = [(piece_text, piece_tokens)]
                    current_chunk_tokens = piece_tokens
                    continue

                candidate = "\n\n".join([p[0] for p in current_chunk_pieces] + [piece_text])
                cand_tokens = count_tokens(candidate)
                if cand_tokens <= MAX_CHUNK_TOKENS:
                    current_chunk_pieces.append((piece_text, piece_tokens))
                    current_chunk_tokens = cand_tokens
                else:
                    chunk_text = "\n\n".join(p[0] for p in current_chunk_pieces)
                    chunks.append({
                        "text": chunk_text,
                        "token_count": current_chunk_tokens,
                        "loc": {
                            "source_id": source_id,
                            "page": physical_page,
                            "page_label": None,
                        },
                    })
                    current_chunk_pieces = [(piece_text, piece_tokens)]
                    current_chunk_tokens = piece_tokens

            if current_chunk_pieces:
                chunk_text = "\n\n".join(p[0] for p in current_chunk_pieces)
                chunks.append({
                    "text": chunk_text,
                    "token_count": current_chunk_tokens,
                    "loc": {
                        "source_id": source_id,
                        "page": physical_page,
                        "page_label": None,
                    },
                })

        if not total_extracted_text.strip():
            raise ValueError("This PDF has no extractable text (it may be scanned).")

        return chunks, page_count
