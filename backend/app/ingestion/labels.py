import re
from dataclasses import dataclass
from typing import Any

import pymupdf


@dataclass(frozen=True)
class _Run:
    start: int
    end: int
    offset: int


def is_licence_page(page: pymupdf.Page, page_dict: dict[str, Any]) -> bool:
    """Detect licence/terms pages according to OCW (<400 chars) and GFDL rules."""
    text_spans = []
    for block in page_dict.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if span_text:
                        text_spans.append(span_text)

    raw_text = " ".join(text_spans).strip()
    norm_text = " ".join(raw_text.lower().split())

    # OCW terms page rule (under 400 characters)
    if "ocw.mit.edu/terms" in norm_text and len(raw_text) < 400:
        return True

    # GFDL notice rule (no character length cap)
    if "gnu free documentation license" in norm_text and "freely redistributable" in norm_text:
        return True

    return False


def _extract_visual_lines(
    page_dict: dict[str, Any],
    rot_mat: pymupdf.Matrix,
    y_tol: float = 3.0,
) -> list[str]:
    """Group text spans into horizontal visual lines in displayed coordinates."""
    raw_lines: list[tuple[pymupdf.Rect, str]] = []
    for block in page_dict.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                spans = [
                    s.get("text", "").strip()
                    for s in line.get("spans", [])
                    if s.get("text", "").strip()
                ]
                line_text = " ".join(spans).strip()
                if not line_text:
                    continue
                disp_bbox = (pymupdf.Rect(line["bbox"]) * rot_mat).normalize()
                raw_lines.append((disp_bbox, line_text))

    if not raw_lines:
        return []

    # Sort raw lines by displayed y0, then x0
    raw_lines.sort(key=lambda item: (item[0].y0, item[0].x0))

    visual_lines: list[tuple[float, float, str]] = []
    curr_group: list[tuple[pymupdf.Rect, str]] = [raw_lines[0]]
    curr_y0 = raw_lines[0][0].y0

    for item in raw_lines[1:]:
        bbox, _ = item
        if abs(bbox.y0 - curr_y0) <= y_tol:
            curr_group.append(item)
        else:
            curr_group.sort(key=lambda x: x[0].x0)
            joined = " ".join(x[1] for x in curr_group)
            visual_lines.append((curr_group[0][0].y0, curr_group[0][0].x0, joined))
            curr_group = [item]
            curr_y0 = bbox.y0

    if curr_group:
        curr_group.sort(key=lambda x: x[0].x0)
        joined = " ".join(x[1] for x in curr_group)
        visual_lines.append((curr_group[0][0].y0, curr_group[0][0].x0, joined))

    # Sort visual lines by displayed y0, then x0
    visual_lines.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in visual_lines]


def _get_line_candidates(line_text: str) -> list[int]:
    """Find standalone arabic numbers 1..9999 at the start or end of a line."""
    candidates = []
    # Match standalone number at start: followed by whitespace or end of line
    m_start = re.match(r"^([0-9]{1,4})(?=\s|$)", line_text)
    if m_start:
        val = int(m_start.group(1))
        if 1 <= val <= 9999:
            candidates.append(val)

    # Match standalone number at end: preceded by whitespace or start of line
    m_end = re.search(r"(?:^|\s)([0-9]{1,4})$", line_text)
    if m_end:
        val = int(m_end.group(1))
        if 1 <= val <= 9999 and val not in candidates:
            candidates.append(val)

    return candidates


def detect_printed_page_number(page_dict: dict[str, Any], rot_mat: pymupdf.Matrix) -> int | None:
    """Detect candidate printed page number from top-most or bottom-most visual line."""
    lines = _extract_visual_lines(page_dict, rot_mat)
    if not lines:
        return None

    top_cands = _get_line_candidates(lines[0])
    if top_cands:
        return top_cands[0]

    bot_cands = _get_line_candidates(lines[-1])
    if bot_cands:
        return bot_cands[0]

    return None


def compute_page_labels(
    doc: pymupdf.Document,
    page_dicts: list[dict[str, Any]],
    licence_pages: set[int],
    has_slide_grid: bool,
) -> list[str | None]:
    """Compute page labels following (a) native labels, (b) printed numbers, (c) None."""
    total_pages = len(doc)

    # Preference (a): native PDF page labels if defined
    native_labels = doc.get_page_labels()
    if native_labels:
        labels: list[str | None] = []
        for p_idx in range(total_pages):
            p_num = p_idx + 1
            if p_num in licence_pages:
                labels.append(None)
            else:
                lbl = doc[p_idx].get_label()
                labels.append(str(lbl) if lbl else None)
        return labels

    # Preference (c): slide_grid set -> None
    if has_slide_grid:
        return [None] * total_pages

    # Preference (b): printed numbers
    raw_detections: dict[int, int] = {}
    for p_idx in range(total_pages):
        p_num = p_idx + 1
        if p_num in licence_pages:
            continue
        rot_mat = doc[p_idx].rotation_matrix
        cand = detect_printed_page_number(page_dicts[p_idx], rot_mat)
        if cand is not None:
            raw_detections[p_num] = cand

    offsets = {p: cand - p for p, cand in raw_detections.items()}
    detected_pages = sorted(offsets.keys())

    # Build contiguous segments of identical offset
    segments: list[list[int]] = []
    if detected_pages:
        curr_seg = [detected_pages[0]]
        for p in detected_pages[1:]:
            if offsets[p] == offsets[curr_seg[-1]]:
                curr_seg.append(p)
            else:
                segments.append(curr_seg)
                curr_seg = [p]
        if curr_seg:
            segments.append(curr_seg)

    # A run is 2 or more detected pages with identical offset and no conflicting detection
    runs: list[_Run] = []
    for seg in segments:
        if len(seg) >= 2:
            runs.append(_Run(start=seg[0], end=seg[-1], offset=offsets[seg[0]]))

    if not runs:
        return [None] * total_pages

    final_labels: list[str | None] = [None] * total_pages
    for p_num in range(1, total_pages + 1):
        if p_num in licence_pages:
            final_labels[p_num - 1] = None
        elif p_num < runs[0].start:
            val = p_num + runs[0].offset
            final_labels[p_num - 1] = str(val) if val >= 1 else None
        elif p_num > runs[-1].end:
            final_labels[p_num - 1] = str(p_num + runs[-1].offset)
        else:
            # Check inside a run
            matching = [r for r in runs if r.start <= p_num <= r.end]
            if matching:
                final_labels[p_num - 1] = str(p_num + matching[0].offset)
            else:
                # Between runs: gets FOLLOWING run's offset
                following = [r for r in runs if r.start > p_num]
                if following:
                    final_labels[p_num - 1] = str(p_num + following[0].offset)
                else:
                    final_labels[p_num - 1] = str(p_num + runs[-1].offset)

    return final_labels
