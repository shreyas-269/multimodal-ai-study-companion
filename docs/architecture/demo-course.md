# Demo course

**Status: chosen in S1; layout facts checked in S2 (2 Oct 2026).** Ingestion rules here are for S4.

## Course

MIT OCW 6.041 / 6.431 Probabilistic Systems Analysis and Applied Probability (Fall 2010, Prof. John Tsitsiklis), lectures 1–6, with slides and recitations (with solutions), plus matching sections of Grinstead & Snell, *Introduction to Probability*: 1.2, 3.1–3.2, 4.1, 5.1, 6.1–6.2.

| # | Topic | Prerequisites | YouTube ID |
| --- | --- | --- | --- |
| 1 | Probability models and axioms | none | j9WZyLZCBzs |
| 2 | Conditioning and Bayes' rule | 1 | TluTv5V0RmE |
| 3 | Independence | 2 | 19Ql_Q3l0GA |
| 4 | Counting | 1 | 6oV3pKLgW2I |
| 5 | Discrete random variables, PMFs and expectation | 1, 4 | 3MOahpLxj6A |
| 6 | Discrete random variable examples and joint PMFs | 2, 5 | -qCEoqpwjf4 |

Every video's `offset_s` is 0: the local files' durations match YouTube within 1 second.

- Numerical answers are exact fractions, so the verifier compares with `fractions.Fraction`.
- Size: about 5 hours of audio; 165 textbook pages; 6 slide decks; recitations of 2–4 pages each.

## Licences

- OCW material is CC BY-NC-SA 4.0.
- The recitations include textbook problems courtesy of Athena Scientific, used with permission.
- Grinstead & Snell is GNU FDL.
- Attribution appears in the README and on in-app source cards (from each source's `licence` and `attribution` fields).

## Files

The course lives outside the repo in `$COURSE_DATA_DIR` (on Shreyas's laptop, `~/course-data/6041/`) and is never committed.

| Item | Files |
| --- | --- |
| Lecture videos | `videos/L01.mp4` … `L06.mp4` (OCW archive.org downloads, about 663 MB) |
| Slides | `slides/L01-slides.pdf` … `L06-slides.pdf` |
| Recitations | `recitations/R01.pdf` … `R06.pdf` and `R01-sol.pdf` … `R06-sol.pdf` |
| Textbook | `textbook/textbook.pdf` (165-page extract); `textbook/grinstead-snell-full.pdf` is the 528-page original and is not in the manifest |
| Syllabus | `syllabus.md`: the six topics with prerequisites |
| Student-made file | `extras/`: one student-made .pptx, added before ingestion, to exercise the PowerPoint → PDF path (no open course ships PowerPoint) |
| Manifest | `manifest.csv`: one row per file |
| Derived files | `derived/` (transcripts, keyframes, checks; created by scripts) |

## manifest.csv

Existing columns: `file, type, youtube_id, offset_s, title, licence, attribution`.

Columns added in S4:

- `source_url`: where the file was downloaded from, for `backend/scripts/fetch_course.py`.
- `slide_grid`: `2x2` for L01–L06 slides; empty for everything else.
- `topics`: one topic ID for a lecture's files (slides, video, recitation, solutions); physical page ranges for textbook.pdf: `2-24=t1;25-69=t4;70-74=t2;75=t2|t3;76-82=t3;83-85=t2;86-99=t2|t3;100-101=t5|t6;102-121=t6;122-136=t5;137=t5|t6;138-139=t6;140-154=t5|t6;155-165=t5` (`a|b` pages are split per chunk by embedding similarity); empty for syllabus.md.
- The three columns were added on 9 Oct (backup: `manifest.backup-2026-10-09.csv`); `source_url` stays empty until S11.

## Textbook page labels

`textbook.pdf` has no page labels of its own. Its pages come from the 528-page original:

| Extract pages | Original pages | Printed page numbers |
| --- | --- | --- |
| 1 | 1 | none (GFDL notice; licence page) |
| 2–24 | 26–48 | 18–40 |
| 25–69 | 85–129 | 77–121 |
| 70–99 | 145–174 | 137–166 |
| 100–121 | 197–218 | 189–210 |
| 122–165 | 239–282 | 231–274 |

Printed number = original page − 8. Checked on extract page 2, whose text starts with "18"; the other ranges assume the same offset.

**Method for page labels (any PDF without its own labels):**

1. Read a page number from the first or last line of each page's text.
2. Fill pages with no number (for example chapter-opening pages) by counting from their neighbours.
3. Restart the count at each jump between ranges.
4. For the demo textbook, check the result against the table above (for example, extract page 25 must be labelled 77).
5. Licence pages get no label.

## Slides

| Deck | Pages | Page size | Layout |
| --- | --- | --- | --- |
| L01 | 4 | 792 × 612 (landscape) | 3 pages of 2×2 slides, then the terms page |
| L02–L06 | 3 each | 612 × 792 (portrait) | 2 pages of 2×2 slides, then the terms page |

Findings from checking the frames with PyMuPDF:

- Counting drawn shapes is unreliable. On L01 one shape can hold several slide frames (page 1 reports one frame covering the whole page), and L02 page 2 has an extra border around the top-right slide.
- So `slide_grid` decides how many slides a page holds and their order: top left, top right, bottom left, bottom right, with slide numbers continuing across pages.
- Highlight rectangles: read the individual rectangle items inside each drawn shape. If their count matches the grid, use them as the bboxes; otherwise divide the page into equal cells.
- Check `page.rotation` before storing a bbox, so it matches what the viewer draws.
- Slide PDFs without a `slide_grid` value are one slide per page. Every page of a converted PPTX is one slide.

## Licence and terms pages

- The last page of every slide deck is MIT OCW's terms page (it starts "MIT OpenCourseWare / http://ocw.mit.edu / 6.041 / 6.431 …"). The recitations end with the same page (confirmed in S4: one licence page each, always the last).
- Textbook page 1 is the GFDL notice.
- Every lecture video opens with a spoken Creative Commons preamble (about 10–34 s); it is dropped before indexing.
- These are detected by their text (`ocw.mit.edu/terms`, the GFDL notice), recorded in the source's `licence_pages`, and excluded from retrieval and citations.

## Recitations

Portrait, 2–4 pages each, cited by page only.
