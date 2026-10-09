import os
from pathlib import Path

import pymupdf
import pytest

from app.ingestion.pdf import parse_pdf
from app.ingestion.slides import extract_cell_boxes

raw_dir = os.environ.get("COURSE_DATA_DIR", "").strip()
if (raw_dir.startswith("'") and raw_dir.endswith("'")) or (
    raw_dir.startswith('"') and raw_dir.endswith('"')
):
    raw_dir = raw_dir[1:-1].strip()
COURSE_DATA_DIR = raw_dir


def _make_pdf_with_pages(pages_content: list[tuple[str, str | None]]) -> bytes:
    """Helper to create synthetic PDF pages with (body_text, header_or_footer)."""
    doc = pymupdf.open()
    for body, edge in pages_content:
        page = doc.new_page(width=612, height=792)
        if edge:
            page.insert_text((50, 50), edge, fontsize=10)
        if body:
            page.insert_textbox(pymupdf.Rect(50, 150, 550, 700), body, fontsize=11)
    return doc.tobytes()


def test_page_labels_runs_and_jump():
    """Verify offset run detection and jump between runs."""
    pages = [
        ("Body 1", "11"),
        ("Body 2", "12"),
        ("Body 3", "13"),
        ("Body 4", "54"),
        ("Body 5", "55"),
        ("Body 6", "56"),
    ]
    data = _make_pdf_with_pages(pages)
    parsed = parse_pdf(data, source_id="test_jump")
    assert parsed.page_labels == ["11", "12", "13", "54", "55", "56"]


def test_page_labels_chapter_opening_gets_following_run():
    """Undetected page between two runs gets the following run's offset."""
    pages = [
        ("Body 1", "11"),
        ("Body 2", "12"),
        ("Chapter 2 Opening without number", None),
        ("Body 4", "54"),
        ("Body 5", "55"),
    ]
    data = _make_pdf_with_pages(pages)
    parsed = parse_pdf(data, source_id="test_chapter")
    assert parsed.page_labels == ["11", "12", "53", "54", "55"]


def test_page_labels_prerun_gap_none():
    """Pages before the first run get offset if result >= 1, else None."""
    pages = [
        ("P1 preamble", None),
        ("P2 preamble", None),
        ("P3 start", "1"),
        ("P4 cont", "2"),
    ]
    data = _make_pdf_with_pages(pages)
    parsed = parse_pdf(data, source_id="test_prerun")
    assert parsed.page_labels == [None, None, "1", "2"]


def test_page_labels_rejects_section_and_outlier():
    """Section number '1.2.' and isolated outlier number are rejected."""
    pages = [
        ("Body 1", "10"),
        ("Body 2 with section 1.2. heading", "1.2. DISCRETE DISTRIBUTIONS"),
        ("Body 3", "12"),
        ("Body 4 with footnote 26", "26 Some footnote text"),
        ("Body 5", "14"),
    ]
    data = _make_pdf_with_pages(pages)
    parsed = parse_pdf(data, source_id="test_noise")
    assert parsed.page_labels == ["10", "11", "12", "13", "14"]


def test_page_labels_prefers_native_labels():
    """PDF with /PageLabels dictionary prefers native document labels."""
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((50, 50), "Preamble")
    doc.new_page(width=612, height=792).insert_text((50, 50), "Chapter 1")
    doc.new_page(width=612, height=792).insert_text((50, 50), "Chapter 2")
    doc.set_page_labels(
        [
            {"startpage": 0, "style": "r"},  # i
            {"startpage": 1, "style": "D", "firstpagenum": 1},  # 1, 2
        ]
    )
    data = doc.tobytes()
    parsed = parse_pdf(data, source_id="test_native")
    assert parsed.page_labels == ["i", "1", "2"]


def test_licence_pages_exclusion():
    """Licence pages are excluded from chunks, have None label, and are recorded."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text(
        (50, 50),
        "GNU Free Documentation License\nThis work is freely redistributable under terms.",
    )
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((50, 50), "Real course content page text.")
    p3 = doc.new_page(width=612, height=792)
    p3.insert_text((50, 50), "MIT OpenCourseWare http://ocw.mit.edu/terms terms of use.")
    data = doc.tobytes()

    parsed = parse_pdf(data, source_id="test_lic")
    assert parsed.licence_pages == [1, 3]
    assert parsed.page_labels[0] is None
    assert parsed.page_labels[2] is None
    for chunk in parsed.chunks:
        assert chunk["loc"]["page"] == 2


def test_ocw_terms_long_page_not_excluded():
    """Page with ocw.mit.edu/terms but >= 400 characters is NOT excluded."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=612, height=792)
    long_content = "Real lecture content discussing statistics and probability models. " * 15
    long_content += " visit http://ocw.mit.edu/terms for more info."
    assert len(long_content) > 400
    p1.insert_textbox(pymupdf.Rect(50, 50, 550, 700), long_content)
    data = doc.tobytes()

    parsed = parse_pdf(data, source_id="test_long_terms")
    assert parsed.licence_pages == []
    assert len(parsed.chunks) > 0


def test_slide_grid_drawing_extraction():
    """Extract 4 cell frames on a 2x2 slide page and chunk per slide."""
    doc = pymupdf.open()
    page = doc.new_page(width=792, height=612)
    rects = [
        pymupdf.Rect(20, 20, 380, 290),
        pymupdf.Rect(400, 20, 760, 290),
        pymupdf.Rect(20, 310, 380, 580),
        pymupdf.Rect(400, 310, 760, 580),
    ]
    for r in rects:
        shape = page.new_shape()
        shape.draw_rect(r)
        shape.finish()
        shape.commit()

    page.insert_text((50, 50), "Slide 1 Content")
    page.insert_text((450, 50), "Slide 2 Content")
    page.insert_text((50, 350), "Slide 3 Content")
    page.insert_text((450, 350), "Slide 4 Content")
    data = doc.tobytes()

    parsed = parse_pdf(data, source_id="test_slides", slide_grid="2x2")
    assert len(parsed.chunks) == 4
    for idx, chunk in enumerate(parsed.chunks, start=1):
        assert chunk["loc"]["slide"] == idx
        assert chunk["loc"]["bbox"] is not None
        assert chunk["loc"]["page_label"] is None


def test_slide_grid_nested_border_deduplication():
    """Nested duplicate border in quadrant is resolved using modal size."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    standard_frames = [
        pymupdf.Rect(20, 20, 290, 380),
        pymupdf.Rect(310, 20, 580, 380),
        pymupdf.Rect(20, 400, 290, 760),
        pymupdf.Rect(310, 400, 580, 760),
    ]
    for r in standard_frames:
        shape = page.new_shape()
        shape.draw_rect(r)
        shape.finish()
        shape.commit()

    outer_nested = pymupdf.Rect(305, 15, 585, 385)
    shape = page.new_shape()
    shape.draw_rect(outer_nested)
    shape.finish()
    shape.commit()

    cells, has_frames = extract_cell_boxes(page, r=2, c=2)
    assert has_frames is True
    assert len(cells) == 4
    top_right = cells[1]
    assert round(top_right[0]) == 310
    assert round(top_right[1]) == 20


def test_slide_grid_equal_cells_fallback():
    """Page with no drawings falls back to equal geometric cells."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    cells, has_frames = extract_cell_boxes(page, r=2, c=2)
    assert has_frames is False
    assert len(cells) == 4
    assert cells[0] == [0.0, 0.0, 306.0, 396.0]
    assert cells[1] == [306.0, 0.0, 612.0, 396.0]
    assert cells[2] == [0.0, 396.0, 306.0, 792.0]
    assert cells[3] == [306.0, 396.0, 612.0, 792.0]


def test_slide_grid_trailing_empty_cell():
    """Trailing cell with neither text nor frame is dropped without consuming slide number."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 50), "Slide 1")
    page.insert_text((350, 50), "Slide 2")
    page.insert_text((50, 450), "Slide 3")
    data = doc.tobytes()

    parsed = parse_pdf(data, source_id="test_trailing", slide_grid="2x2")
    slides_found = [c["loc"]["slide"] for c in parsed.chunks]
    assert slides_found == [1, 2, 3]


def test_rotated_page_and_cropbox_offset():
    """Rotated page (/Rotate 90) with non-zero CropBox transforms to displayed page.rect."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    frame = pymupdf.Rect(70, 50, 540, 740)
    shape = page.new_shape()
    shape.draw_rect(frame)
    shape.finish()
    shape.commit()

    page.set_cropbox(pymupdf.Rect(50, 30, 562, 762))
    page.set_rotation(90)
    page.insert_text((100, 100), "Rotated and Cropped")
    data = doc.tobytes()

    parsed = parse_pdf(data, source_id="test_rot", slide_grid="1x1")
    assert len(parsed.chunks) > 0
    assert parsed.page_count == 1

    assert parsed.chunks[0]["loc"]["bbox"] == [22.0, 20.0, 712.0, 490.0]


def test_scanned_pdf_fails_cleanly():
    """PDF with no extractable text raises ValueError."""
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    data = doc.tobytes()

    with pytest.raises(ValueError, match="no extractable text"):
        parse_pdf(data, source_id="test_scanned")


def test_text_conservation_and_ids():
    """All non-space characters preserved in chunks, IDs deterministic, tokens <= 400."""
    sample_text = "Probability theory is the branch of mathematics concerning probability. " * 30
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_textbox(pymupdf.Rect(50, 50, 550, 750), sample_text)
    data = doc.tobytes()

    parsed = parse_pdf(data, source_id="src_cons")
    assert len(parsed.chunks) >= 1
    for idx, chunk in enumerate(parsed.chunks):
        assert chunk["id"] == f"src_cons-{idx:05d}"
        assert chunk["token_count"] <= 400

    extracted_chars = "".join(c["text"].replace(" ", "").replace("\n", "") for c in parsed.chunks)
    original_chars = sample_text.replace(" ", "").replace("\n", "")
    assert extracted_chars == original_chars


# ============================================================================
# REAL-FILE TESTS (Skipped if COURSE_DATA_DIR unset or files missing)
# ============================================================================


def _course_file(relative: str) -> Path | None:
    if not COURSE_DATA_DIR:
        return None
    p = Path(COURSE_DATA_DIR) / relative
    return p if p.exists() else None


@pytest.mark.skipif(not _course_file("textbook/textbook.pdf"), reason="textbook.pdf missing")
def test_real_textbook_all_164_labels():
    """All 164 content pages match demo-course.md printed page table."""
    path = _course_file("textbook/textbook.pdf")
    assert path is not None
    data = path.read_bytes()
    parsed = parse_pdf(data, source_id="textbook")

    assert parsed.page_count == 165
    assert parsed.licence_pages == [1]
    assert parsed.page_labels[0] is None

    # Expected: 2-24=p+16, 25-69=p+52, 70-99=p+67, 100-121=p+89, 122-165=p+109
    expected: list[str | None] = [None] * 165
    for p in range(2, 25):
        expected[p - 1] = str(p + 16)
    for p in range(25, 70):
        expected[p - 1] = str(p + 52)
    for p in range(70, 100):
        expected[p - 1] = str(p + 67)
    for p in range(100, 122):
        expected[p - 1] = str(p + 89)
    for p in range(122, 166):
        expected[p - 1] = str(p + 109)

    for idx, (act, exp) in enumerate(zip(parsed.page_labels, expected, strict=False)):
        assert act == exp, f"Page {idx + 1}: expected {exp}, got {act}"


@pytest.mark.skipif(not _course_file("slides/L01-slides.pdf"), reason="course files missing")
def test_real_licence_pages_all_course_files():
    """Verify licence page detection on all real slide decks and recitations."""
    checks = [
        ("slides/L01-slides.pdf", [4]),
        ("slides/L02-slides.pdf", [3]),
        ("slides/L03-slides.pdf", [3]),
        ("slides/L04-slides.pdf", [3]),
        ("slides/L05-slides.pdf", [3]),
        ("slides/L06-slides.pdf", [3]),
        ("recitations/R01.pdf", [2]),
        ("recitations/R01-sol.pdf", [2]),
        ("recitations/R02.pdf", [2]),
        ("recitations/R02-sol.pdf", [4]),
        ("recitations/R03.pdf", [3]),
        ("recitations/R03-sol.pdf", [2]),
        ("recitations/R04.pdf", [3]),
        ("recitations/R04-sol.pdf", [3]),
        ("recitations/R05.pdf", [3]),
        ("recitations/R05-sol.pdf", [3]),
        ("recitations/R06.pdf", [2]),
        ("recitations/R06-sol.pdf", [3]),
    ]
    for rel_path, expected_licence in checks:
        path = _course_file(rel_path)
        assert path is not None, f"Missing {rel_path}"
        parsed = parse_pdf(path.read_bytes(), source_id="src_check")
        assert parsed.licence_pages == expected_licence, (
            f"{rel_path}: got {parsed.licence_pages}, expected {expected_licence}"
        )


@pytest.mark.skipif(not _course_file("slides/L01-slides.pdf"), reason="slides missing")
def test_real_slides_counts_and_frames():
    """Verify slide counts (L01: 12, L02-L06: 8) and assert 4 frames per content page."""
    deck_expected = [
        ("slides/L01-slides.pdf", 12),
        ("slides/L02-slides.pdf", 8),
        ("slides/L03-slides.pdf", 8),
        ("slides/L04-slides.pdf", 8),
        ("slides/L05-slides.pdf", 8),
        ("slides/L06-slides.pdf", 8),
    ]
    for rel_path, expected_count in deck_expected:
        path = _course_file(rel_path)
        assert path is not None
        data = path.read_bytes()
        doc = pymupdf.open(stream=data, filetype="pdf")

        for p_idx in range(len(doc) - 1):
            cells, has_frames = extract_cell_boxes(doc[p_idx], r=2, c=2)
            assert has_frames is True, f"{rel_path} p{p_idx + 1} failed to find frames"
            assert len(cells) == 4, f"{rel_path} p{p_idx + 1} frame count {len(cells)} != 4"

        parsed = parse_pdf(data, source_id="slide_test", slide_grid="2x2")
        slides_present = {c["loc"]["slide"] for c in parsed.chunks}
        assert max(slides_present) == expected_count, (
            f"{rel_path} max slide {max(slides_present)} != {expected_count}"
        )


@pytest.mark.skipif(not _course_file("slides/L02-slides.pdf"), reason="L02-slides.pdf missing")
def test_real_l02_slide_line_separation():
    """L02 slide 1 contains 'LECTURE 2' and does NOT contain 'Review of probability models'."""
    path = _course_file("slides/L02-slides.pdf")
    assert path is not None
    data = path.read_bytes()
    parsed = parse_pdf(data, source_id="l02_test", slide_grid="2x2")

    slide_1_chunks = [c for c in parsed.chunks if c["loc"]["slide"] == 1]
    slide_1_text = " ".join(c["text"] for c in slide_1_chunks)

    assert "LECTURE 2" in slide_1_text
    assert "Review of probability models" not in slide_1_text
