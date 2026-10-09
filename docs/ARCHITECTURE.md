# Architecture

**Status: v1.3 (9 Oct 2026, after S4 Ingestion).** Checkpoint 1 passed on 6 Oct; S4's protected scope (page, slide and timestamp citations, topics, the demo notebook with video) passed on 9 Oct. The data model, API contract, libraries and repo layout change only through a new line in DECISIONS.md.

## How these docs are organised

Read this file and `docs/DECISIONS.md` first, every session. Then read the file in `docs/architecture/` for the area your task touches.

| File | Covers |
| --- | --- |
| `architecture/data-model.md` | Firestore collections, shared types (Location, Citation, Paragraph, Message), Storage paths, IDs, indexes, security rules |
| `architecture/api-contract.md` | API conventions, access rules, every endpoint and the slice that builds it, key request and response shapes |
| `architecture/stack.md` | Libraries, repo layout, local and hosted environments, environment variables |
| `architecture/demo-course.md` | The demo course, its files and manifest.csv, page labels, slide layout, licence pages |

Each fact lives in exactly one file. This file summarises and links; it never repeats a table from another file. Later slices add their own files here (for example grounding, learner model, evaluation).

## Product

A source-grounded AI study companion. Students upload lecture videos, textbooks, slides and notes; the app turns them into a source-cited knowledge base, tutors from it with exact citations, quizzes the student with verified questions, and (if they opt in to Study Coach) tracks per-topic mastery.

**Out of scope:** Hindi or mixed-language support, audio tutoring, and everything cut on 6 Oct (see "Scope after the Replan").

## Scope after the Replan (6 Oct)

The Replan on 6 Oct moved the dates and cut scope; each decision is a 6 Oct line in DECISIONS.md.

- **Dates:** checkpoint 2 (video ingestion + verified quizzes) Sat 10 Oct (video done 9 Oct; the quiz part is S6 and may slip a day); feature freeze Sun 11 Oct evening; checkpoint 3 (first evaluation scores) Mon 12 Oct; submission Wed 14 Oct evening.
- **Protected:** grounded citations (page, slide, timestamp), video ingestion, verified quizzes (MCQ and numerical), Study Coach core (BKT, needs-work, progress), evaluation scores, the hosted demo.
- **Should, in order, only while on schedule:** keyframe and figure descriptions (I3c), PPTX/DOCX upload conversion (I1c), short-answer questions, the diagnostic quiz, the prerequisite rerank boost, simulated students, a small quiz on a user's own upload.
- **Cut:** the job queue, subtopics and knowledge items, the tree panel (folders, drag and drop, renaming), `#` references, pasted images, reply quotes, notes files, roadmap, schedule generator, Excel/HTML/website ingestion.
- **Review depth follows risk:** see DECISIONS.md (6 Oct).

## Components

| Part | Technology |
| --- | --- |
| Backend API | Python 3.12 + FastAPI in one container; the hosted platform is decided in S11 (Cloud Run needs billing, which is unavailable) |
| Frontend | Next.js (TypeScript) on Vercel Hobby |
| Login | Firebase Authentication: email/password and guest (anonymous) |
| Database + vector search | Firestore (accessed only by the backend); the real project stays on the Spark plan |
| File storage | The Storage emulator locally; hosted storage is decided in S11, behind `backend/app/storage.py` |
| App AI model | Gemini Flash models on the free Gemini API tier, through the google-genai SDK, as a model chain: `GEMINI_MODEL` first, then `GEMINI_FALLBACK_MODELS` |
| Transcription | faster-whisper medium.en (CPU, int8), on the laptop only (`backend/scripts/transcribe_lectures.py`) |
| Embeddings | fastembed with BAAI/bge-small-en-v1.5 (384 dimensions), on the laptop and in the backend container |
| Video | YouTube links with `&t=<seconds>s`; no video hosting |
| Evaluation | RAGAS or Claude Code as the judge (S9 decides), a small testset, within the call budget |

Full library list and versions: `architecture/stack.md`.

## Access and environments

- The frontend uses Firebase only to sign in. It sends the Firebase ID token to the backend on every request and never reads Firestore or Storage itself. Firestore and Storage security rules deny all client access; the backend uses the Admin SDK.
- The frontend never talks to Gemini and contains no business logic. `frontend/lib/api.ts` is the only file that calls the backend.
- Local development uses the Firebase Emulator Suite in Docker. The hosted demo uses the real Firebase project study-companion-d049d on the Spark plan for Auth and Firestore; the backend host and file storage are decided in S11. Environment variables decide which one the backend talks to.
- The demo notebook is shared: every signed-in user can read its content, and each user gets their own chats, checkboxes, quizzes and mastery (see "Shared content, private state" in `architecture/data-model.md`).
- Locally, exactly one backend server runs. On Windows two uvicorn processes can both listen on port 8000 and requests then go to either one, so no agent starts a backend and nobody starts a second one.

## Ingestion pipeline (per source)

1. Normalise: PDF as is; video → timestamped transcript (faster-whisper, laptop only), with the spoken OCW preamble dropped; the Markdown syllabus → the six topics. Licence and terms pages are detected by their text and excluded from retrieval and citations. Not built in v1 (Shoulds, after the freeze only if time allows): PowerPoint and Word → PDF with LibreOffice (I1c), keyframes and their Gemini descriptions (I3c).
2. Chunk, recording each chunk's exact location (see Location in `architecture/data-model.md`). Slide handouts with several slides per page are split per slide using the `slide_grid` manifest column, and each slide chunk carries its bbox in the page's displayed (rotated) coordinates; other slide PDFs are one slide per page. Chunks hold at most 400 tokens, counted with the embedding model's own tokenizer (its limit is 512), and never cross a page or a slide. Transcript chunks are consecutive Whisper segments, never split, at most 60 s and 400 tokens, and keep their segments internally for citation refinement.
3. Not built in v1 (I3c, a Should): describe keyframes (and figures) with Gemini vision, 5 images per call.
4. Tag every chunk to one of the six syllabus topics without Gemini: manifest.csv's topics column decides (one topic per lecture file; physical page ranges for the textbook), and embedding similarity to the topic descriptions splits pages marked "a|b". Student uploads are not tagged, and student notebooks have no topics in v1. There are no subtopics and no knowledge items.
5. Embed with bge-small-en-v1.5; store chunks + vectors in Firestore.
6. The question bank is built per topic by a laptop script (S6).

Every source is processed once and every model output is cached. The demo course, including video, is ingested only on the laptop (`backend/scripts/ingest_course.py`, run from `backend/` with `uv run --env-file ../.env python scripts/ingest_course.py`, `--force` to re-ingest), which fills each source's licence, attribution, youtube_id and offset_s from manifest.csv. It is emulator-only and re-runnable, and builds 26 sources (6 slide decks, 12 recitations, the textbook, the syllabus, 6 lectures) and 713 chunks. User uploads are processed inside the upload request, on the hosted demo too (no job queue); a video upload on the hosted demo explains that videos are ingested on the laptop only.

**S4 state (9 Oct):** uploads accept PDFs only, up to 30 MB, processed inside the request, with printed page labels, licence-page exclusion and page-bounded chunks; slide splitting needs a slide_grid, so only the demo's slide handouts are split into slides with bboxes. The demo notebook is built by `ingest_course.py`. Not built: PPTX/DOCX (I1c), keyframes (I3c). The question bank comes in S6.

## Chat request flow

1. Retrieve: one nearest-neighbour search over the notebook's chunks (top 40), then a rerank in Python that boosts the chat's topic and its prerequisites. With no topic, the whole notebook is searched equally.
2. Send Gemini the chunks, the format template, the student's custom instructions and, if Study Coach is on, one line about weak prerequisites.
3. Gemini returns paragraphs, each citing the chunks it came from. A paragraph with no citation is marked `outside_course` and shown in the "Beyond your course" box.
4. `POST /v1/notebooks/{nb}/ask` returns the paragraphs **and** the retrieved context; the evaluation harness calls it. Chat messages run the same function, then save the message and record a chat signal for the learner model.

**S4 state (9 Oct):** `/ask` embeds the question with BGE's query instruction, takes the top 40 chunks by cosine distance, drops chunks of sources that aren't ready, and keeps the top 10 with at most 5 video chunks (backfilled with video only when fewer than 10 others exist); there is no rerank yet (S5 adds the topic and prerequisite boost, the format template and the custom instructions). The chunks go to Gemini as numbered `<<<SOURCE n>>>` blocks headed with the title and the page, or "at m:ss" for video, with the question last; Gemini cites numbers, which the backend maps to chunk IDs, dropping unknown numbers and chunks with neither a page nor a time. `build_citation` in `backend/app/chat/citations.py` builds every citation: "{title} p. {printed page}" (plus " (slide n)" and open.bbox on slides) for PDFs, and "{title}, m:ss" with a YouTube link for video, whose start is refined to the 2-segment window that best matches the citing paragraph (local embeddings, no Gemini). With `allow_outside` false (the default), a question the sources don't answer gets one uncited "not covered" paragraph.

**Model chain (L1):** all Gemini calls go through `backend/app/llm/`, with JSON output against a Pydantic schema. The cache is checked under every model in the chain (`GEMINI_MODEL`, then `GEMINI_FALLBACK_MODELS`) before any call. Each model gets up to 3 attempts on 5xx errors, timeouts and connection errors; a 429 moves to the next model at once and, with 2 or more models, skips that model until its retry time; any other 4xx stops the chain (a logged 500). One 150 s deadline per request caps every attempt's HTTP timeout. The first success is cached under the answering model's key, and `/ask` reports that model. If every model is out of quota, the answer is 429 `quota_exhausted`; otherwise 503 `unavailable`.

## Long-running work (no job queue)

There is no job queue in v1 (cut on 6 Oct). Uploads are processed inside the request, which takes up to about 2 minutes for a textbook. Long work runs as laptop scripts that are safe to re-run: transcription (`backend/scripts/transcribe_lectures.py`, about 3.5 h for the six lectures), the demo-notebook build (`backend/scripts/ingest_course.py`, about 2.5 min from scratch), the question-bank build (S6) and the evaluation run (S9). Deterministic chunk IDs and the LLM cache make a re-run resume instead of repeating work or spending quota. The `jobs` collection, `GET /v1/jobs/{job}` and `POST /internal/jobs/run` are not built.

## Design rules

- **Stable IDs** for every topic and chat; names are display labels only. Chunk IDs are deterministic (`{source_id}-{seq:05d}`), so re-running ingestion overwrites instead of duplicating.
- **Topics are fixed:** the six syllabus topics `t1`–`t6` (IDs from the topic number in syllabus.md) plus `other`, with prerequisites from syllabus.md. Each topic stores one location per page, slide or 5-minute video window, so its Sources count equals its chips. Only the demo notebook has topics in v1.
- **Citations point to original sources.** Every citation, from `/ask`, chat or the Sources button, is built by `build_citation` in `backend/app/chat/citations.py`, so labels and links are identical everywhere.
- **The Pydantic models in `backend/app/models/` are the API contract.** Frontend TypeScript types are generated from the backend's OpenAPI schema, never written by hand.
- **Question bank, verified on entry:** numericals checked by running Python (`fractions.Fraction` for exact answers), MCQs by a blind second-model answer, short answers by a source-linked rubric. A seen-questions record per student prevents repeats.
- **Study Coach is a sealed module** behind eight calls: `record_quiz_answer`, `record_chat_signal`, `record_checkbox`, `get_topics_needing_work`, `pick_quiz_questions`, `build_report`, `start_diagnostic`, `get_progress`. When Study Coach is off, the record calls do nothing and `get_topics_needing_work` falls back to unchecked checkboxes. The module takes a storage interface (Firestore in the app, in-memory for simulated students), and the BKT maths is pure functions.
- **BKT parameters (fixed):** prior 0.3; guess 0.25 (MCQ), 0.1 (short answer), 0.05 (numerical); slip 0.1; learn 0.15. A checkbox tick lifts a topic to at least 0.8. Forgetting is applied when mastery is read.
- **Firestore is shaped by how screens read data:** documents in collections, no joins, some data stored twice so each screen loads in one query.
- **No colour-coding** anywhere in the UI.
- **Access checks:** every notebook route goes through `get_readable_notebook` (owner, or the demo) or `get_owned_notebook` (owner only) in `backend/app/api/access.py`. A notebook you can't see is 404, never 403; 403 means readable but not yours. Notebook and source IDs, and list cursors, are checked with a full-string match before any Firestore read.
- **Tests never call real Gemini:** an autouse fixture replaces the Gemini client with a fake, so any call a test hasn't mocked fails, and the suite passes identically with a fake API key. Tests mock the fake client's `models.generate_content` and raise the SDK's real error classes, so the retry logic stays under test. Tests delete only the documents they created and never wipe the emulator.

## Cost rules (also architecture rules)

1. Process every source once; cache every model output.
2. Transcription and embeddings run locally and never use API requests.
3. Batch generation: one request generates several questions or describes several images.
4. Long work runs as re-runnable laptop scripts that resume from deterministic IDs and the cache instead of restarting.
5. Simulated students never call the AI; they answer in code from the existing question bank, using the Study Coach with in-memory storage.
6. Never put Gemini calls or Firestore reads inside unbounded loops.
7. Builds and test runs never call the real Gemini API; every live check states its call budget (usually 1 to 3 calls), and each workload stays within the call budgets in DECISIONS.md (6 Oct).
8. The free tier's per-model daily quota is the real budget: every call goes through the model chain, and bulk scripts may put the lighter models first with `models=`.

## Acceptance criteria

Written by Shreyas before each feature is built (3–5 observable checks each). A feature is done only when every check passes.

### Citations (example)

- Upload `L02-slides.pdf`, ask "what is Bayes' rule?": the answer cites a source.
- Select a paragraph, click **Source**: the PDF opens on the exact page, and that page actually states Bayes' rule.
- Ask "what's the capital of France?": the answer is declined or sits entirely in the "Beyond your course" box.

### Checkpoint 1 (passed 6 Oct)

- Create a notebook; it's listed after reload; a guest in incognito can't see it, and its URL shows "Notebook not found."
- textbook.pdf reaches ready in under 2 minutes; chunks have pages and embeddings; a .docx gets a clear "only PDF for now" message; the emulator vector test passes.
- "What is conditional probability?" returns a cited paragraph whose citation opens the PDF at a page that discusses it.
- "What's the capital of France?" returns no cited paragraph.
- The /ask response contains context with chunk text, page and score; asking twice writes nothing except llm_cache.

### Model chain (L1, passed 7 Oct)

- With `GEMINI_FALLBACK_MODELS` unset, behaviour is identical to before (the existing tests pass unchanged).
- A 429 from the first model makes the next model answer, and `/ask` reports that model.
- No request runs past 150 s, whatever the models do (fake-clock simulation).
- A repeated question is served from the cache in under 1 s with no Gemini call.

### S4 Ingestion (passed 9 Oct)

- All 165 textbook page labels match demo-course.md's table; textbook page 1 and the last page of every slide deck and recitation are licence pages with no chunks.
- The L01–L06 slide handouts split into 52 slides, and a cited slide's bbox frames exactly one slide on the rendered page (including L02 page 2's nested border).
- ingest_course.py builds nb_demo_6041 with 26 ready sources and 713 chunks, none untagged; a second run changes nothing.
- GET /topics returns t1–t6 in order; t2's Sources lists at least two kinds of source, with slide bboxes and video links, and its item count equals location_count.
- The L02 radar question cites the lecture video with a link within 10 s of the first mention of "radar" (measured: t=1505 s against 1514.87 s).

### Checkpoint 2 (target Sat 10 Oct)

Status on 9 Oct: the backend side of the first four checks passes (S4); the quiz check waits for S6, and the slide highlight in the browser is lane B's F2b.

- The demo notebook lists every source in manifest.csv as ready, each card showing its licence and attribution.
- A question about an example shown in a lecture cites the video; its YouTube link opens within 10 s of where the example starts.
- A slide citation opens the right page of the slide handout, with the cited slide highlighted.
- The Sources button on a topic lists citations from at least two kinds of source (textbook, slides, recitation, video).
- A quiz on one topic contains MCQ and numerical questions that all passed verification; a wrong answer gets feedback with a citation.

## Frontend (state on 9 Oct)

- Next.js 16 (App Router, TypeScript) in `frontend/`, styled with Tailwind and shadcn/ui's base-nova style, which is built on Base UI rather than Radix (no `asChild`). Monochrome throughout; the font is Geist from next/font (`--font-sans` maps to `--font-geist-sans`).
- Pages: `/login` (email/password sign-in, create account, continue as guest); `/notebooks` (full account bar, the Study Coach first-launch card while `study_coach` is null, create notebook, paged list with the demo notebook first); `/settings` (Study Coach on/off, custom instructions up to 2,000 characters, empty saved as null); `/notebooks/[id]`, a three-panel workspace from 1024 px: a header bar (back link, name, Demo badge, status, compact account line), sources as cards with licence and attribution plus "Add a source" on the left, the ask box and answers in the centre, and the PDF viewer on the right only while a source is open; below 1024 px the panels stack and opening a citation scrolls to the viewer.
- Data access: `lib/api.ts` is the only file that builds backend requests, and every function takes its types from `lib/api-types.ts` by operation ID. TanStack Query keys always include the user's uid; queries run only once Firebase auth has loaded (`use-require-user`); the cache is cleared on sign-out; every `PATCH /v1/me` goes through `lib/use-patch-me.ts`, which writes the returned user into `["me", uid]`. `lib/api-types.ts` is regenerated only when `backend/` has no uncommitted changes (last: after SRC1, 3db5468).
- PDF viewer: react-pdf 11 (pdfjs-dist 6.3) with a bundled worker, loaded client-only. Files come from `/file` with Range requests and a fresh, never-cached token; pdf.js doing that fetch is the one documented exception to the `lib/api.ts` rule. Page numbers are physical pages, the same as `Location.page` and `Citation.open.page`.
- Answers: react-markdown with remark-math and rehype-katex, never raw HTML; `\( \)` and `\[ \]` are converted to `$` and `$$` first. Every `outside_course` paragraph goes in the "Beyond your course" box. PDF citations call `openSource(source_id, page)`; YouTube citations open `open.url` in a new tab. The frontend never builds a URL and never retries `/ask`.
- Timeouts: upload 3 minutes (processing happens inside the request), ask 150 seconds.
- Built since 7 Oct: F2b (slide highlight from `open.bbox`), FX2 (name Groundwork, polish), F2c (topic list with per-topic Sources). Not built yet: F3 (continuous chat per topic in the centre panel, after CH1), F4 (quiz UI), F5 (progress, needs-work, checkboxes). The drag-and-drop tree panel is cut.