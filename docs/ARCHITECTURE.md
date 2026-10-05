# Architecture

**Status: v1.1 (5 Oct 2026, S3 lane A: C1–C3 built).** The data model, API contract, libraries and repo layout are final. Change them only through a new line in DECISIONS.md.

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

**Out of scope:** Hindi or mixed-language support, audio tutoring.

## Components

| Part | Technology |
| --- | --- |
| Backend API | Python 3.12 + FastAPI, one container on Cloud Run |
| Frontend | Next.js (TypeScript) on Vercel Hobby |
| Login | Firebase Authentication: email/password and guest (anonymous) |
| Database + vector search | Firestore (accessed only by the backend) |
| File storage | Cloud Storage for Firebase (accessed only by the backend) |
| App AI model | Gemini Flash (text + vision) through the google-genai SDK |
| Transcription | faster-whisper, on the laptop only |
| Embeddings | fastembed with BAAI/bge-small-en-v1.5 (384 dimensions), on the laptop and in the backend container |
| Video | YouTube links with `&t=<seconds>s`; no video hosting |
| Evaluation | RAGAS (to confirm in S9), Gemini Flash as judge |

Full library list and versions: `architecture/stack.md`.

## Access and environments

- The frontend uses Firebase only to sign in. It sends the Firebase ID token to the backend on every request and never reads Firestore or Storage itself. Firestore and Storage security rules deny all client access; the backend uses the Admin SDK.
- The frontend never talks to Gemini and contains no business logic. `frontend/lib/api.ts` is the only file that calls the backend.
- Local development uses the Firebase Emulator Suite in Docker. The hosted demo uses the real Firebase project. Environment variables decide which one the backend talks to.
- The demo notebook is shared: every signed-in user can read its content, and each user gets their own chats, checkboxes, quizzes and mastery (see "Shared content, private state" in `architecture/data-model.md`).
- Locally, exactly one backend server runs, in lane A's backend tab. On Windows two uvicorn processes can both listen on port 8000 and requests then go to either one, so no agent and no other window starts a backend.

## Ingestion pipeline (per source)

1. Normalise: PowerPoint and Word → PDF (LibreOffice); video → timestamped transcript (faster-whisper) + keyframes at scene changes (ffmpeg); Markdown syllabus → headings. Licence and terms pages are detected by their text and excluded from retrieval and citations. Excel, HTML and websites are S10 extras.
2. Chunk, recording each chunk's exact location (see Location in `architecture/data-model.md`). Slide handouts with several slides per page are split per slide using the `slide_grid` manifest column; other slide PDFs are one slide per page. Chunks hold at most 400 tokens, counted with the embedding model's own tokenizer (its limit is 512), and never cross a page.
3. Read figures and diagrams with Gemini Flash vision.
4. Extract knowledge items (definitions, formulas, theorems, worked examples, figures, facts, edge cases) and tag each chunk and item to a subtopic, in batched calls. A syllabus, if given, seeds the topic tree.
5. Embed with bge-small-en-v1.5; store chunks + vectors in Firestore.
6. Background jobs build and verify the question bank per subtopic.

Every source is processed once and every model output is cached. Video ingestion runs only on the laptop (`backend/scripts/ingest_course.py`); on the hosted demo, video upload explains this instead of processing. PDF, PPTX, DOCX and Markdown uploads are processed on the hosted demo through the job queue.

**Checkpoint-1 state (S3):** only PDFs are accepted, up to 30 MB, and they are processed inside the upload request: text per physical page with PyMuPDF in its native reading order, page-bounded chunks with IDs `{source_id}-{seq:05d}` from 00000, embeddings from bge-small-en-v1.5, written in batches of up to 500. A failed upload returns status "failed" with a short error, deletes its partial chunks and keeps its original file for a retry. Steps 3, 4 and 6, page labels, licence-page exclusion and slide splitting arrive in S4, which also moves processing into the job queue.

## Chat request flow

1. Retrieve: one nearest-neighbour search over the notebook's chunks (top 40), then a rerank in Python that boosts the chat's folder topic and its prerequisites, plus any `#`-referenced notes file (searched, not pasted whole). A chat's folder topic is its nearest ancestor in the tree that is a real topic; with none, the whole notebook is searched equally.
2. Send Gemini the chunks, the format template, the student's instructions and, if Study Coach is on, one line about weak prerequisites.
3. Gemini returns paragraphs, each citing the chunks it came from. A paragraph with no citation is marked `outside_course` and shown in the "Beyond your course" box.
4. `POST /v1/notebooks/{nb}/ask` returns the paragraphs **and** the retrieved context; the evaluation harness calls it. Chat messages run the same function, then save the message and record a chat signal for the learner model.

**Checkpoint-1 state (S3):** `/ask` embeds the question with BGE's query instruction, takes the top 40 chunks by cosine distance, drops chunks of sources that aren't ready, and sends the top 10 without reranking; S5 adds the rerank, the topic and prerequisite boost, `#` references and the format template. The chunks go to Gemini as numbered `<<<SOURCE n>>>` blocks with the question last; Gemini cites numbers, which the backend maps to chunk IDs, dropping unknown numbers and chunks without a page. With `allow_outside` false (the default), a question the sources don't answer gets one uncited "not covered" paragraph. All Gemini calls go through `backend/app/llm/`: JSON output against a Pydantic schema, retries only on 5xx, timeouts and connection errors (at most 3 attempts or 100 s), 429 → `quota_exhausted` (never retried), persistent failure → 503 `unavailable`, and an `llm_cache` key that includes a hash of the full prompt.

## Background jobs

Long work (source ingestion on the hosted demo, question bank building, complete notes) is a document in the `jobs` collection. One runner function claims due jobs, runs them with retries and backoff, and resumes partly finished work instead of restarting. A Gemini 429 response puts a job in `waiting_quota` with a later `next_run_at`. On the laptop the runner is a script; on Cloud Run it is the endpoint `POST /internal/jobs/run`, called by a scheduler (set up in S11). Clients poll status every 2–3 seconds.

## Design rules

- **Stable IDs** for every topic, chat and notes file; names and positions are display labels only. Chunk and knowledge-item IDs are deterministic (`{source_id}-{seq}`), so re-running ingestion overwrites instead of duplicating.
- **Moving or renaming in the tree never changes content.** A topic's prerequisites and tags stay the same wherever the student puts it.
- **Citations point to original sources**, never to generated notes. Citations through a `#` notes file pass through to the original sources.
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
3. Batch generation: one request generates several questions or items.
4. Background jobs run through the rate-limit-aware queue that retries and resumes instead of restarting.
5. Simulated students never call the AI; they answer in code from the existing question bank, using the Study Coach with in-memory storage.
6. Never put Gemini calls or Firestore reads inside unbounded loops.
7. Builds and test runs never call the real Gemini API; every live check states its call budget (usually 1 to 3 calls).

## Acceptance criteria

Written by Shreyas before each feature is built (3–5 observable checks each). A feature is done only when every check passes.

### Citations (example)

- Upload `L02-slides.pdf`, ask "what is Bayes' rule?": the answer cites a source.
- Select a paragraph, click **Source**: the PDF opens on the exact page, and that page actually states Bayes' rule.
- Ask "what's the capital of France?": the answer is declined or sits entirely in the "Beyond your course" box.
