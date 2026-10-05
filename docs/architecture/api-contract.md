# API contract

**Status: final (2 Oct 2026, S2).** The Pydantic models in `backend/app/models/` are the authoritative contract; this file is the plan they implement. Shared types (Location, Citation, Paragraph, Message) are defined in `data-model.md`.

## Conventions

- Every path starts with `/v1`, except `/internal/...`.
- JSON bodies with snake_case names; file uploads are multipart. Timestamps are ISO 8601 in UTC.
- Every request sends `Authorization: Bearer <Firebase ID token>`. The backend verifies it with firebase-admin.
- Errors are `{"error": {"code": "...", "message": "..."}}`:

| HTTP | code | When |
| --- | --- | --- |
| 401 | `unauthenticated` | Missing or invalid token |
| 403 | `forbidden` | Signed in but not allowed (e.g. uploading to the demo notebook) |
| 404 | `not_found` | Does not exist, or a notebook you cannot see |
| 409 | `not_ready` | The source or notebook is still processing |
| 422 | `invalid` | Bad input |
| 429 | `quota_exhausted` | Gemini quota hit; includes `retry_after_s` |
| 503 | `unavailable` | The AI model is temporarily unavailable after retries; includes a Retry-After header |

- Slow work returns `202` with a status object; clients poll every 2–3 seconds. No WebSockets, and chat answers are not streamed in v1.
- Lists accept `?limit=&cursor=` and return `{items, next_cursor}`.
- CORS allows only the origins in `CORS_ORIGINS`.
- OpenAPI operation IDs are `{tag}_{function}` (e.g. `health_get`) and must be unique; a test enforces this, because they become the frontend's generated type names.
- Error codes include `method_not_allowed` (405). 500s pass through a middleware inside CORS, so error responses still carry CORS headers.

## Access rules

- Read a notebook's content: its owner, or anyone if `is_demo` is true.
- Write a notebook's content (upload sources, generate notes): owner only.
- `members/{uid}` data: only that user.

## Endpoints

| Method and path | Does | Built in |
| --- | --- | --- |
| `GET /v1/health` (no auth) | Liveness check | S3 |
| `GET /v1/me` | Returns the user, creating the document on first call | S3 |
| `PATCH /v1/me` | Sets `study_coach` and `format` | S3 |
| `GET /v1/notebooks` | Your notebooks plus the demo notebook | S3 |
| `POST /v1/notebooks` | `{name}` → notebook | S3 |
| `GET /v1/notebooks/{nb}` | Notebook with `sources_summary` | S3 |
| `POST /v1/notebooks/{nb}/sources` | Multipart file (+ `role`), or JSON `{youtube_url}` → 202 + Source | S3 (PDF, processed inside the request), S4 (all kinds, through jobs) |
| `GET /v1/notebooks/{nb}/sources` | Sources with status | S3 |
| `GET /v1/notebooks/{nb}/sources/{src}` | One source | S3 |
| `GET /v1/notebooks/{nb}/sources/{src}/file` | Streams the viewer PDF (supports Range requests) | S3 |
| `GET /v1/notebooks/{nb}/topics/{t}/sources` | The Sources button: the topic's locations as Citations | S4 |
| `GET /v1/jobs/{job}` | Job status and progress | S4 |
| `POST /v1/notebooks/{nb}/ask` | Stateless answer; nothing saved, no learner signal. Used by the evaluation harness | S3 (minimal), S5 |
| `POST /v1/notebooks/{nb}/chats` | `{name?, folder_node_id}` → chat | S5 |
| `GET /v1/notebooks/{nb}/chats/{c}/messages` | Chat history, paginated | S5 |
| `POST /v1/notebooks/{nb}/chats/{c}/messages` | Same pipeline as `/ask`, then saves both messages and records a chat signal | S5 |
| `GET /v1/notebooks/{nb}/question-bank` | Counts per topic, type and status | S6 |
| `POST /v1/notebooks/{nb}/quizzes` | `{mode, topic_ids?, count}` → quiz with questions, without answer keys | S6 |
| `GET /v1/notebooks/{nb}/quizzes/{q}` | Quiz state | S6 |
| `POST /v1/notebooks/{nb}/quizzes/{q}/answers` | `{question_id, answer, time_ms}` → Feedback | S6 |
| `POST /v1/notebooks/{nb}/quizzes/{q}/finish` | Review: `build_report` when Study Coach is on, a plain score summary when off | S6 / S7 |
| `POST /v1/notebooks/{nb}/diagnostic` | `{action: "start" \| "skip"}` → quiz or status | S7 |
| `GET /v1/notebooks/{nb}/progress` | `get_progress` | S7 |
| `GET /v1/notebooks/{nb}/needs-work` | `get_topics_needing_work` | S7 |
| `GET /v1/notebooks/{nb}/workspace` | Tree + topics (with prerequisites) + chat and notes names, in one call | S8 |
| `PATCH /v1/notebooks/{nb}/workspace/nodes/{id}` | `{name?, parent_id?, order?}`: rename, move, nest | S8 |
| `POST /v1/notebooks/{nb}/workspace/folders` | `{name, parent_id}` → folder node | S8 |
| `PUT /v1/notebooks/{nb}/topics/{t}/checked` | `{checked}`; also calls `record_checkbox` | S8 |
| `POST /v1/uploads/images` | Pasted image (multipart) → `{path}` | S8 |
| `POST /v1/notebooks/{nb}/notes-files` | `{kind, from_file_id?}` → 202 + notes file | S10 |
| `GET /v1/notebooks/{nb}/notes-files/{f}` | Notes file with sections as they fill in | S10 |
| `GET /v1/notebooks/{nb}/roadmap?topic_id=` | Nodes and edges | S10 |
| `POST /v1/notebooks/{nb}/schedule` | Deadlines → plan | S10 (cuttable) |
| `POST /internal/jobs/run` | Runs due jobs within a time budget. Authenticated by the `X-Jobs-Secret` header (value `JOBS_RUNNER_SECRET`), not a user token | S4 / S11 |

## Key shapes

**Ask** (`POST /v1/notebooks/{nb}/ask`)

```text
request:  { question, topic_id?, refs? {sources: [source_id], files: [node_id]},
            allow_outside?: bool, pasted_images?: [path] }
response: { paragraphs: [Paragraph], context: [{chunk_id, text, loc, score}],
            model, latency_ms }
```

**Chat message** (`POST /v1/notebooks/{nb}/chats/{c}/messages`)

```text
request:  { text, refs?, reply_to? {message_id, quote}, pasted_images?: [path],
            allow_outside?: bool }
response: { user_message: Message, assistant_message: Message }   # assistant_message includes context
```

**Source** (returned by upload and list)

```text
{ id, ref_n, title, kind, role, status, stage, error?, page_count?, duration_s?, job_id? }
```

**Quiz question** (as sent to the student; answer keys never leave the backend before answering)

```text
{ id, type, topic_id, difficulty, stem, options?: [{id, text}] }
```

**Feedback** (`POST .../answers`)

```text
{ verdict: correct | incorrect | partial, correct_answer, explanation,
  citations: [Citation], misconception?, rubric_coverage?: [{point, met}] }
```

**Job** (`GET /v1/jobs/{job}`)

```text
{ id, type, status, progress {done, total}, error?, next_run_at? }
```
