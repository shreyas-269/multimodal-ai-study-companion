# Data model

**Status: final (2 Oct 2026, S2).** Change only through DECISIONS.md. All Firestore paths are built in `backend/app/db/`; no other module writes path strings.

## Shared content, private state

- **Notebook content** (sources, chunks, topics, questions) belongs to the notebook. Only the notebook's owner, through ingestion and jobs, writes it.
- **Learner state** (checkboxes, chats, quizzes, attempts, mastery) lives under `notebooks/{nb}/members/{uid}`, one subtree per user.
- A notebook with `is_demo: true` can be read by every signed-in user. Each user still gets their own `members/{uid}` subtree, so judges never see or overwrite each other's chats or mastery, and nothing has to be copied.

## IDs

| Thing | ID |
| --- | --- |
| Users | Firebase Auth UID |
| Demo notebook | `nb_demo_6041` (fixed) |
| Demo-notebook sources | `src_` + the file's lower-cased stem, other characters → `_` (`src_l02_slides`, `src_textbook`, `src_l02`) |
| Other notebooks, sources, questions, chats, messages, quizzes, attempts | Firestore auto-IDs |
| Chunks | `{source_id}-{seq:05d}` (deterministic) |
| Topics | `t1`–`t6` from the topic number in syllabus.md, plus `other`. Never regenerated |

## Collections: shared content

| Path | Fields |
| --- | --- |
| `users/{uid}` | email, display_name, is_guest, study_coach (true / false / null = not asked yet), format {custom_instructions}, created_at |
| `notebooks/{nb}` | name, owner_uid, is_demo, status (empty / processing / ready), sources_summary [{source_id, ref_n, title, kind, status}] (stored twice so the notebook page loads in one read), counts {chunks, items, questions_verified}, created_at |
| `notebooks/{nb}/sources/{src}` | ref_n (the n in `@n`), title, kind (pdf / slides_pdf / pptx / docx / video / markdown), role (content / syllabus), filename, storage_path, viewer_path (the PDF the viewer opens: the original for PDFs, the converted file for PPTX/DOCX), youtube_id, offset_s, duration_s, page_count, page_labels [str or null, one per page], slide_grid ("2x2" etc. or null), licence_pages [page numbers], licence, attribution, status (processing / ready / failed), stage, error, ingest_version, created_at |
| `notebooks/{nb}/topics/{topic}` | name, order, prerequisite_ids [], summary, is_other, locations [{chunk_id, loc}] (the Sources button: one per page, slide or 5-minute video window; stored twice), location_count (the number of locations) |
| `notebooks/{nb}/chunks/{chunk}` | source_id, kind (text / transcript / figure / keyframe), text (for figures and keyframes, the vision description), loc (Location), topic_id, embedding (vector, 384 dimensions), token_count, image_path, segments [{start, end, text}] (transcript chunks only; internal, never returned by the API) |
| `notebooks/{nb}/questions/{q}` | type (mcq / short / numerical), topic_id, difficulty (1–3), stem, options [{id, text, misconception or null}], answer ({option_id} or {value: "3/8"} or {model_answer}), rubric [{point, chunk_id}], explanation, citations [Citation], verification {method, passed, detail}, status (verified / rejected), batch_id, created_at |

## Collections: private learner state

| Path | Fields |
| --- | --- |
| `notebooks/{nb}/members/{uid}` | checked {topic_id: true}, seen_question_ids [], diagnostic {status (not_started / in_progress / skipped / done), quiz_id}, created_at. Created on first open. Created only if absent (ensure_member, ref.create), never overwritten. |
| `.../members/{uid}/chats/{chat}` | name, topic_id (null for the whole notebook), message_count, created_at, updated_at |
| `.../chats/{chat}/messages/{msg}` | A Message (below). Stores retrieved chunk IDs and scores, not chunk text. Also stores seq (consecutive per chat, internal, never returned); a turn's two messages are written together only after the answer succeeds. |
| `.../members/{uid}/quizzes/{quiz}` | mode (adaptive / chosen / diagnostic), topic_ids, question_ids, position, status, score, report, created_at |
| `.../members/{uid}/attempts/{a}` | question_id, quiz_id, topic_id, type, answer, correct, score (0–1), time_ms, created_at |
| `.../members/{uid}/coach_events/{e}` | kind (quiz_answer / chat_signal / checkbox), topic_id, value (quiz_answer: the score 0–1; chat_signal: 1; checkbox: 1 tick, 0 untick), created_at. Written only by the Study Coach. IDs: `qa_{attempt_id}` and `cs_{message_id}` are dedupe markers (the qa_ one is written in the same transaction as the mastery update); checkbox events use auto-IDs |
| `.../members/{uid}/coach_events/{e}` | kind (chat_signal / checkbox), topic_id, value, created_at. Written only by the Study Coach |

## Collections: top level

| Path | Fields |
| --- | --- |
| `llm_cache/{sha256}` | model, prompt_version, output, created_at. Key = sha256 of the model, the prompt version, a hash of the full prompt and the caller's cache-key parts, joined by `\x1f`. Written only after a valid response; a hit writes nothing. Laptop scripts also mirror the cache to `backend/.cache/` (git-ignored). The stored model is the model that answered (L1). |

## Shared types

These are Pydantic models in `backend/app/models/`; the frontend gets them as generated TypeScript types.

```text
Location {
  source_id
  page?        1-based physical page of the source's viewer PDF (what the viewer opens)
  page_label?  printed page number for display, or null
  slide?       1-based slide number within the deck, in reading order
  bbox?        [x0, y0, x1, y1] in PDF points, top-left origin, in the page's displayed (rotated) coordinates
  t_start_s?, t_end_s?   seconds from the start of the video file (before offset_s)
  section?     heading text, e.g. "§4.1 Discrete Conditional Probability"
  url?, anchor?          reserved for HTML and websites (S10)
  sheet?, cell_range?    reserved for Excel (S10)
}

Citation {
  chunk_id
  loc: Location
  label        display text (formats below)
  open         {kind: "pdf", source_id, page, bbox?} | {kind: "youtube", url}
}

Paragraph {
  id
  section?     a heading of the default format: before_you_start, why_it_exists, how_it_works,
               worked_example, common_mistakes, key_takeaways, beyond_your_course
  text         Markdown + LaTeX
  citations: [Citation]
  outside_course: bool      true → shown in the "Beyond your course" box
  images: [Image]
}

Image {
  kind: extracted | diagram | ai_generated | search_link
  url, caption, citation?
}

Message {
  id, role (user | assistant), created_at
  text?                      user messages
  paragraphs?                assistant messages
  refs {sources: [source_id]}
  context?: [{chunk_id, text, loc, score}]    returned by the API; stored as IDs and scores only
}
```

The backend builds `open`; the frontend never builds a link. A YouTube `url` is `https://www.youtube.com/watch?v={youtube_id}&t={offset_s + floor(t_start_s)}s`.

**Citation label formats:**

| Source | Label |
| --- | --- |
| Textbook | `Grinstead & Snell p. 133` (printed page; physical page if no label; no § section in v1) |
| Slide handout | `L03 slides p. 2 (slide 5)` |
| Recitation | `R03 solutions p. 2` |
| Video | `L03 lecture, 12:34` (for /ask, the start is refined to the best-matching segments) |
| Converted PPTX | `<title>, slide 4` |

Licence and terms pages are never cited.

## Storage paths

```
notebooks/{nb}/sources/{src}/original.{ext}
notebooks/{nb}/sources/{src}/viewer.pdf          PPTX and DOCX conversions
notebooks/{nb}/sources/{src}/figures/{chunk_id}.png
notebooks/{nb}/sources/{src}/keyframes/{chunk_id}.jpg
```

Locally these live in the Storage emulator; the hosted storage is decided in S11, and only `backend/app/storage.py` touches it.

Videos are never stored. Laptop-only derived files (transcripts, keyframes before upload) go in `$COURSE_DATA_DIR/derived/`, outside the repo.

## Indexes (`infra/firestore.indexes.json`)

- Vector index on collection group `chunks`, field `embedding`, 384 dimensions, cosine distance.
- `questions`: topic_id, status, difficulty.
- `notebooks`: owner_uid ascending, created_at descending.

## Security rules (`infra/firestore.rules`, `infra/storage.rules`)

Deny all reads and writes from clients. Only the backend (Admin SDK) reads and writes data.
