# Data model

**Status: final (2 Oct 2026, S2).** Change only through DECISIONS.md. All Firestore paths are built in `backend/app/db/`; no other module writes path strings.

## Shared content, private state

- **Notebook content** (sources, chunks, knowledge items, topics, questions, notes files) belongs to the notebook. Only the notebook's owner, through ingestion and jobs, writes it.
- **Learner state** (tree layout, names, checkboxes, chats, quizzes, attempts, mastery) lives under `notebooks/{nb}/members/{uid}`, one subtree per user.
- A notebook with `is_demo: true` can be read by every signed-in user. Each user still gets their own `members/{uid}` subtree, so judges never see or overwrite each other's chats or mastery, and nothing has to be copied.

## IDs

| Thing | ID |
| --- | --- |
| Users | Firebase Auth UID |
| Demo notebook | `nb_demo_6041` (fixed) |
| Other notebooks, sources, questions, chats, messages, quizzes, attempts, notes files, folders, jobs | Firestore auto-IDs |
| Chunks | `{source_id}-{seq:05d}` (deterministic) |
| Knowledge items | `{source_id}-k{seq:05d}` (deterministic) |
| Topics | Generated once and never regenerated; the "Other material" topic is `other`. The demo course's topic IDs are seeded deterministically from syllabus.md (S4) |
| Tree nodes | Topic nodes use the topic ID, chat nodes the chat ID, notes nodes the notes-file ID, user folders an auto-ID |

## Collections: shared content

| Path | Fields |
| --- | --- |
| `users/{uid}` | email, display_name, is_guest, study_coach (true / false / null = not asked yet), format {custom_instructions}, created_at |
| `notebooks/{nb}` | name, owner_uid, is_demo, status (empty / processing / ready), sources_summary [{source_id, ref_n, title, kind, status}] (stored twice so the notebook page loads in one read), counts {chunks, items, questions_verified}, created_at |
| `notebooks/{nb}/sources/{src}` | ref_n (the n in `@n`), title, kind (pdf / slides_pdf / pptx / docx / video / markdown; later html / xlsx / web), role (content / syllabus), filename, storage_path, viewer_path (the PDF the viewer opens: the original for PDFs, the converted file for PPTX/DOCX), youtube_id, offset_s, duration_s, page_count, page_labels [str or null, one per page], slide_grid ("2x2" etc. or null), licence_pages [page numbers], licence, attribution, status (queued / processing / ready / failed), stage, error, ingest_version, created_at |
| `notebooks/{nb}/topics/{topic}` | name, parent_id (null for top level), level (topic / subtopic), order, prerequisite_ids [] (a subtopic with none inherits its parent's), summary, is_other, locations [Location] (the Sources button; stored twice), item_count |
| `notebooks/{nb}/chunks/{chunk}` | source_id, kind (text / transcript / figure / keyframe), text (for figures and keyframes, the vision description), loc (Location), topic_id, embedding (vector, 384 dimensions), token_count, image_path |
| `notebooks/{nb}/knowledge_items/{item}` | type (definition / formula / theorem / worked_example / figure / fact / edge_case), title, text (Markdown + LaTeX), topic_id, chunk_ids [], loc |
| `notebooks/{nb}/questions/{q}` | type (mcq / short / numerical), topic_id, difficulty (1–3), stem, options [{id, text, misconception or null}], answer ({option_id} or {value: "3/8"} or {model_answer}), rubric [{point, chunk_id}], explanation, citations [Citation], verification {method, passed, detail}, status (verified / rejected), batch_id, created_at |
| `notebooks/{nb}/notes_files/{f}` | kind (complete / revision), default_name ("YYYY-MM-DD HH-MM"), from_file_id, created_by, status, sources_used [], coverage {covered, total}, source_fingerprint (hash of source IDs and versions; drives the "nothing changed" warning), created_at |
| `notebooks/{nb}/notes_files/{f}/sections/{topic_id}` | order, markdown, citations [Citation], status. One document per section, so sections fill in progressively and no file reaches Firestore's 1 MB document limit |

## Collections: private learner state

| Path | Fields |
| --- | --- |
| `notebooks/{nb}/members/{uid}` | tree {node_id: {kind (topic / folder / chat / notes), name, parent_id, order, checked}}, seen_question_ids [], diagnostic {status (not_started / in_progress / skipped / done), quiz_id}, created_at. Created on first open from the topics; topics added later are merged in when it is read |
| `.../members/{uid}/chats/{chat}` | name, folder_node_id, created_at, updated_at |
| `.../chats/{chat}/messages/{msg}` | A Message (below). Stores retrieved chunk IDs and scores, not chunk text |
| `.../members/{uid}/quizzes/{quiz}` | mode (adaptive / chosen / diagnostic), topic_ids, question_ids, position, status, score, report, created_at |
| `.../members/{uid}/attempts/{a}` | question_id, quiz_id, topic_id, type, answer, correct, score (0–1), time_ms, created_at |
| `.../members/{uid}/mastery/{topic_id}` | p_known, n_obs, last_updated. Written only by the Study Coach |
| `.../members/{uid}/coach_events/{e}` | kind (chat_signal / checkbox), topic_id, value, created_at. Written only by the Study Coach |

## Collections: top level

| Path | Fields |
| --- | --- |
| `jobs/{job}` | type (ingest_source / build_questions / generate_notes), notebook_id, target_id, status (queued / running / waiting_quota / done / failed), attempts, next_run_at, lease_until, progress {done, total}, error, created_by, created_at |
| `llm_cache/{sha256}` | model, output, created_at. Key = hash of model + prompt + inputs. Laptop scripts also mirror the cache to `backend/.cache/` (git-ignored) |

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
  refs {sources: [source_id], files: [node_id]}
  reply_to? {message_id, quote}
  pasted_images: [storage path]
  context?: [{chunk_id, text, loc, score}]    returned by the API; stored as IDs and scores only
}
```

The backend builds `open`; the frontend never builds a link. A YouTube `url` is `https://www.youtube.com/watch?v={youtube_id}&t={offset_s + floor(t_start_s)}s`.

**Citation label formats:**

| Source | Label |
| --- | --- |
| Textbook | `Grinstead & Snell p. 133 (§4.1)` (printed page; physical page if no label) |
| Slide handout | `L03 slides, p. 2 (slide 5)` |
| Recitation | `R03 solutions, p. 2` |
| Video | `L03 lecture, 12:34` |
| Converted PPTX | `<title>, slide 4` |
| Pasted image | `provided by you` |

Licence and terms pages are never cited.

## Storage paths

```
notebooks/{nb}/sources/{src}/original.{ext}
notebooks/{nb}/sources/{src}/viewer.pdf          PPTX and DOCX conversions
notebooks/{nb}/sources/{src}/figures/{chunk_id}.png
notebooks/{nb}/sources/{src}/keyframes/{chunk_id}.jpg
users/{uid}/pasted/{id}.png
```

Videos are never stored. Laptop-only derived files (transcripts, keyframes before upload) go in `$COURSE_DATA_DIR/derived/`, outside the repo.

## Indexes (`infra/firestore.indexes.json`)

- Vector index on collection group `chunks`, field `embedding`, 384 dimensions, cosine distance.
- `jobs`: status ascending, next_run_at ascending.
- `questions`: topic_id, status, difficulty.
- `notebooks`: owner_uid ascending, created_at descending.

## Security rules (`infra/firestore.rules`, `infra/storage.rules`)

Deny all reads and writes from clients. Only the backend (Admin SDK) reads and writes data.
