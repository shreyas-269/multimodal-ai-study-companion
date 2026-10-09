from collections import Counter
from typing import Any

import pymupdf


def extract_cell_boxes(
    page: pymupdf.Page,
    r: int,
    c: int,
) -> tuple[list[list[float]], bool]:
    """Extract slide cell bounding boxes from drawings, or fall back to equal cells.

    Returns (cells, has_drawn_frames).
    """
    W = page.rect.width
    H = page.rect.height
    cell_w = W / c
    cell_h = H / r
    cell_area = cell_w * cell_h
    rot_mat = page.rotation_matrix

    raw_candidates: list[pymupdf.Rect] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] == "re":
                disp_rect = (pymupdf.Rect(item[1]) * rot_mat).normalize()
                if disp_rect.width * disp_rect.height >= 0.65 * cell_area:
                    raw_candidates.append(disp_rect)

    if raw_candidates:
        # Determine modal frame dimension (round(w), round(h))
        modal_dims = Counter(
            (round(rect.width), round(rect.height)) for rect in raw_candidates
        ).most_common(1)[0][0]
        target_w, target_h = modal_dims

        # Group by grid quadrant
        quadrants: dict[tuple[int, int], list[pymupdf.Rect]] = {}
        for rect in raw_candidates:
            cx = (rect.x0 + rect.x1) / 2.0
            cy = (rect.y0 + rect.y1) / 2.0
            col_idx = max(0, min(c - 1, int(cx // cell_w)))
            row_idx = max(0, min(r - 1, int(cy // cell_h)))
            key = (row_idx, col_idx)
            quadrants.setdefault(key, []).append(rect)

        selected_frames: list[pymupdf.Rect] = []
        for row_idx in range(r):
            for col_idx in range(c):
                q_cands = quadrants.get((row_idx, col_idx), [])
                if not q_cands:
                    continue
                if len(q_cands) == 1:
                    selected_frames.append(q_cands[0])
                else:
                    # Deduplicate nested/duplicate borders:
                    # prefer match with modal dims, then closest area
                    def _score(rect: pymupdf.Rect) -> tuple[int, float]:
                        matches_modal = (
                            round(rect.width) == target_w and round(rect.height) == target_h
                        )
                        is_modal = 0 if matches_modal else 1
                        area_diff = abs((rect.width * rect.height) - (target_w * target_h))
                        return (is_modal, area_diff)

                    q_cands.sort(key=_score)
                    selected_frames.append(q_cands[0])

        if len(selected_frames) == r * c:
            # Sort into rows using y-tolerance, then sort by x
            y_tol = 0.4 * cell_h
            selected_frames.sort(key=lambda item: (round(item.y0 / y_tol), item.x0))
            return [[f.x0, f.y0, f.x1, f.y1] for f in selected_frames], True

    # Fallback to equal cells
    equal_cells: list[list[float]] = []
    for row_idx in range(r):
        for col_idx in range(c):
            equal_cells.append(
                [
                    col_idx * cell_w,
                    row_idx * cell_h,
                    (col_idx + 1) * cell_w,
                    (row_idx + 1) * cell_h,
                ]
            )
    return equal_cells, False


def assign_lines_to_cells(
    page_dict: dict[str, Any],
    rot_mat: pymupdf.Matrix,
    cells: list[list[float]],
) -> list[list[str]]:
    """Assign text lines to cells by displayed center point, falling back to nearest cell."""
    cell_rects = [pymupdf.Rect(box) for box in cells]
    cell_lines: list[list[str]] = [[] for _ in cells]

    for block in page_dict.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                line_spans = [s.get("text", "") for s in line.get("spans", [])]
                line_text = "".join(line_spans).strip()
                if not line_text:
                    continue

                disp_bbox = (pymupdf.Rect(line["bbox"]) * rot_mat).normalize()
                center = pymupdf.Point(
                    (disp_bbox.x0 + disp_bbox.x1) / 2.0,
                    (disp_bbox.y0 + disp_bbox.y1) / 2.0,
                )

                # Check containing cell
                assigned_idx: int | None = None
                for idx, c_rect in enumerate(cell_rects):
                    if c_rect.contains(center):
                        assigned_idx = idx
                        break

                if assigned_idx is None:
                    # Assign to nearest cell by Euclidean distance to center
                    min_dist = float("inf")
                    for idx, c_rect in enumerate(cell_rects):
                        ccx = (c_rect.x0 + c_rect.x1) / 2.0
                        ccy = (c_rect.y0 + c_rect.y1) / 2.0
                        dist = (center.x - ccx) ** 2 + (center.y - ccy) ** 2
                        if dist < min_dist:
                            min_dist = dist
                            assigned_idx = idx

                if assigned_idx is not None:
                    cell_lines[assigned_idx].append(line_text)

    return cell_lines


def parse_slides_on_page(
    page: pymupdf.Page,
    page_dict: dict[str, Any],
    r: int,
    c: int,
    start_slide: int,
) -> tuple[list[tuple[int, list[float], list[str]]], int]:
    """Parse slides on a single non-licence page.

    Returns (slides, next_slide_num) where each slide is (slide_num, bbox, lines).
    """
    cells, has_drawn_frames = extract_cell_boxes(page, r, c)
    cell_lines = assign_lines_to_cells(page_dict, page.rotation_matrix, cells)

    slides: list[tuple[int, list[float], list[str]]] = []
    curr_slide = start_slide

    for box, lines in zip(cells, cell_lines, strict=False):
        has_text = any(line.strip() for line in lines)
        has_frame = has_drawn_frames

        # A cell with neither text nor a frame is not a slide (trailing cells)
        if not has_text and not has_frame:
            continue

        slides.append((curr_slide, box, lines))
        curr_slide += 1

    return slides, curr_slide
