# Architecture

**Status: DRAFT v0 (1 Oct 2026).** The data model, API contract and per-feature acceptance criteria are finalised in the architecture session on 2 Oct. Until then, sections marked *provisional* may change; ask before building on them.

## Product

A source-grounded AI study companion. Students upload lecture videos, textbooks, slides and notes; the app turns them into a source-cited knowledge base, tutors from it with exact citations, quizzes the student with verified questions, and (if they opt in to Study Coach) tracks per-topic mastery.

**Out of scope:** Hindi or mixed-language support, audio tutoring.

## Repository layout *(provisional)*

```
/
├── backend/      Python + FastAPI. ALL application logic. Runs on Cloud Run.
│   └── scripts/  One-off laptop jobs (e.g. ingesting the demo course)
├── frontend/     Next.js (React). Calls the backend API only. Hosted on Vercel.
├── eval/         Evaluation harness. A client of the backend API, like the frontend.
├── docs/         ARCHITECTURE, DECISIONS, HOW_IT_WORKS
├── AGENTS.md     Rules for Antigravity (and any agent)
├── CLAUDE.md     Rules for Claude Code
├── .env.example  Names of required secrets (no values)
└── docker-compose.yml   One-command local run (added in the skeleton step)
```

The frontend never talks to Gemini directly and contains no business logic.

## Components

| Part | Technology |
| --- | --- |
| Backend API | Python, FastAPI, one container on Cloud Run |
| Frontend | Next.js on Vercel Hobby |
| Login | Firebase Authentication |
| Database + vector search | Firestore |
| File storage | Cloud Storage for Firebase |
| App AI model | Gemini Flash (text + vision) |
| Transcription | faster-whisper, run locally |
| Embeddings | Open-source model run locally and inside the backend container; the same model for indexing and queries |
| Video | YouTube links with `&t=<seconds>s`; no video hosting |
| Evaluation | RAGAS or DeepEval, Gemini Flash as judge |

*Proposed libraries, to confirm on 2 Oct:* PyMuPDF (PDF), headless LibreOffice (Word/PowerPoint → PDF), faster-whisper, React Flow (roadmap).

## Ingestion pipeline (per source)

1. Normalise: Word and PowerPoint → PDF; video → timestamped transcript + keyframes at scene changes; HTML and websites → text with section anchors; Excel → sheets.
2. Chunk, recording each chunk's exact location (page, slide, timestamp, URL section, or sheet and cell range).
3. Read figures and diagrams with Gemini Flash vision.
4. Extract knowledge items (definitions, formulas, theorems, worked examples, figures, facts, edge cases) and tag each chunk and item to a subtopic, in batched calls. A syllabus, if given, seeds the topic tree.
5. Embed locally; store chunks + vectors in Firestore.
6. Background jobs build and verify the question bank per subtopic.

Every source is processed once and every model output is cached.

## Chat request flow

1. Retrieve chunks: the chat's folder (subtopic + its prerequisites) first, then the whole notebook, plus any `#`-referenced notes file (searched, not pasted whole).
2. Send Gemini the chunks, the format template, the student's instructions and, if Study Coach is on, one line about weak prerequisites.
3. Gemini answers with a hidden source marker per paragraph. Unmarked paragraphs are shown as outside the course ("Beyond your course").
4. The API returns the answer **and** the retrieved context (so evaluation can score it), and records a chat signal for the learner model.

## Design rules

- **Stable IDs** for every topic, chat and notes file; names and positions are display labels only.
- **Citations point to original sources**, never to generated notes. Citations through a `#` notes file pass through to the original sources.
- **Question bank, verified on entry:** numericals checked by running Python, MCQs by a blind second-model answer, short answers by a source-linked rubric. A seen-questions record per student prevents repeats.
- **Study Coach is a sealed module** behind eight calls: `record_quiz_answer`, `record_chat_signal`, `record_checkbox`, `get_topics_needing_work`, `pick_quiz_questions`, `build_report`, `start_diagnostic`, `get_progress`. When Study Coach is off, the record calls do nothing and `get_topics_needing_work` falls back to unchecked checkboxes.
- **BKT parameters (fixed):** prior 0.3; guess 0.25 (MCQ), 0.1 (short answer), 0.05 (numerical); slip 0.1; learn 0.15. A checkbox tick lifts a topic to at least 0.8. Forgetting is applied when mastery is read.
- **Firestore is shaped by how screens read data:** documents in collections, no joins, some data stored twice so each screen loads in one query.
- **No colour-coding** anywhere in the UI.

## Firestore collections *(provisional)*

| Collection | Holds |
| --- | --- |
| users | settings, Study Coach on/off, format preferences |
| notebooks | name, syllabus, source list |
| sources | file metadata, Storage path or YouTube URL, processing status |
| chunks | text, location, topic ID, embedding |
| knowledge_items | item type, text, topic ID, location |
| topics | stable ID, name, parent, order, prerequisites, checkbox |
| chats / messages | folder (topic ID), messages with citation markers |
| notes_files | snapshot name, sources used, coverage, sections |
| questions | type, text, answer key, options with misconception tags, topic, source, difficulty |
| attempts | student, question, answer, correct, time |
| mastery | student, topic ID, probability known, last updated |

## Cost rules (also architecture rules)

1. Process every source once; cache every model output.
2. Transcription and embeddings run locally and never use API requests.
3. Batch generation: one request generates several questions or items.
4. Background jobs run through a rate-limit-aware queue that retries and resumes instead of restarting.
5. Simulated students never call the AI; they answer in code from the existing question bank.
6. Never put Gemini calls or Firestore reads inside unbounded loops.

## API contract

*To be written on 2 Oct.*

## Acceptance criteria

Written by Shreyas before each feature is built (3–5 observable checks each). A feature is done only when every check passes.

### Citations (example)

- Upload `textbook.pdf`, ask "what is a circular queue?": the answer cites a source.
- Select a paragraph, click **Source**: the PDF opens on the exact page, and that page actually discusses circular queues.
- Ask "what's the capital of France?": the answer is declined or sits entirely in the "Beyond your course" box.