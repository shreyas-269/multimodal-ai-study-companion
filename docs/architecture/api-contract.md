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
| 429 | `quota_exhausted` | Every model in the chain is out of quota; includes `retry_after_s` |
| 503 | `unavailable` | No model in the chain could answer (overloaded, timing out or out of time); includes a Retry-After header |

- Uploads return `202` with the source's final status; they're processed inside the request, so there's no polling. No WebSockets, and chat answers are not streamed in v1.
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
| `POST /v1/notebooks/{nb}/sources` | Multipart file (+ `role`) → 202 + Source | S3 (PDF, processed inside the request), S4 (PPTX/DOCX → PDF, if built) |
| `GET /v1/notebooks/{nb}/sources` | Sources with status | S3 |
| `GET /v1/notebooks/{nb}/sources/{src}` | One source | S3 |
| `GET /v1/notebooks/{nb}/sources/{src}/file` | Streams the viewer PDF (supports Range requests) | S3 |
| `GET /v1/notebooks/{nb}/topics` | The flat topic list (shape below) | S4 |
| `GET /v1/notebooks/{nb}/topics/{t}/sources` | The Sources button: the topic's locations as Citations, built by the same function as `/ask` | S4 |
| `POST /v1/notebooks/{nb}/ask` | Stateless answer; nothing saved, no learner signal. Used by the evaluation harness | S3 (minimal), S5 |
| `POST /v1/notebooks/{nb}/chats` | `{name?, topic_id?}` → chat | S5 |
| `GET /v1/notebooks/{nb}/chats` | The caller's chats in this notebook, newest `updated_at` first, no topic filter (`chats_list`) | S5 |
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
| `PUT /v1/notebooks/{nb}/topics/{t}/checked` | `{checked}`; also calls `record_checkbox` | S7 |

## Key shapes

**Ask** (`POST /v1/notebooks/{nb}/ask`)

```text
request:  { question, topic_id?, refs? {sources: [source_id]}, allow_outside?: bool }
          # topic_id: t1-t6 or other (boosts that topic's chunks); refs.sources is accepted but not used for retrieval
response: { paragraphs: [Paragraph], context: [{chunk_id, text, loc, score}],
            model, latency_ms }
```

**Chat message** (`POST /v1/notebooks/{nb}/chats/{c}/messages`)

```text
request:  { text (1-2,000 chars, trimmed), refs? {sources: [source_id] (at most 50, each 1-128 chars)}, allow_outside?: bool }
response: { user_message: Message, assistant_message: Message, model, latency_ms }
# operation ID chats_send (200); assistant_message carries the full context; nothing is saved when the answer fails
# history (chats_list_messages) returns the same Message shape with context null
```

**Chat** (`chats_create` 201, `chats_list`, `chats_list_messages`)

```text
{ id, name, topic_id (t1-t6, other or null), created_at, updated_at, message_count }
# POST body {name? (1-100 chars), topic_id?}; default name: the topic's name or "Whole notebook"
# chats_list: ?limit (1-50, default 20) & cursor (a chat ID) -> {items, next_cursor}
# chats_list_messages: ?limit (1-100, default 30) & cursor (a message ID); pages newest first,
#   items oldest first within a page; next_cursor = the oldest message's ID in the page, or null
```

**Source** (returned by upload and list)

```text
{ id, ref_n, title, kind, role, status, stage, error?, page_count?, duration_s?, licence?, attribution?, youtube_url? }
```

**Topic list** (`GET /v1/notebooks/{nb}/topics`)

```text
{ items: [ { id, name, order, summary, is_other, prerequisite_ids: [topic_id], location_count } ],
  next_cursor: null }
# sorted by order; no paging parameters; readable by the owner, or anyone for the demo notebook
```

**Topic sources** (`GET /v1/notebooks/{nb}/topics/{t}/sources`, operation ID `topics_list_sources`; the topic list's operation ID is `topics_list`)

```text
{ items: [Citation], next_cursor: null }
# one Citation per stored location (page, slide or 5-minute video window), built by build_citation;
# a topic ID that isn't t1–t6 or other, or doesn't exist → 404; student notebooks have no topics
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
