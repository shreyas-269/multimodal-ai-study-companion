# Deployment plan (S11 / T6)

**Status: proposal, awaiting Shreyas's approval.** Nothing has been deployed or changed in the app. Prices and free-tier limits below are from memory and **must be re-checked on each vendor's pricing page before committing** (marked ⚠).

## 1. Reconnaissance: what the repo actually says

| Finding | Where | Consequence |
| --- | --- | --- |
| `storage.py` is hard-wired to the Google Cloud Storage client (`google.cloud.storage`). Six functions are used: `upload_bytes`, `get_object_size`, `stream_byte_range`, `stream_object`, `delete_prefix`, `get_storage_bucket`. | `backend/app/storage.py` | R2 is S3-compatible, **not** GCS-compatible, so the GCS client cannot talk to it. Both options need a small change inside `storage.py` (see §3). |
| The only runtime reader is `GET .../sources/{src}/file`; it calls `get_object_size` then `stream_object` / `stream_byte_range`. | `backend/app/api/sources.py:180-346` | The backend only needs **read** access in production. Uploads (`upload_bytes`) happen from laptop scripts (`ingest_course.py`) and the upload endpoint (`sources.py:86`). |
| `viewer_path` is stored in the Firestore source document. | `sources.py:200` | Whatever backs storage must serve the same relative paths (`notebooks/{nb}/...`). |
| `FIREBASE_STORAGE_BUCKET` is a **required** setting even if unused. | `backend/app/config.py:26` | Set a placeholder in production if the bucket is never touched. |
| `JOBS_RUNNER_SECRET` is still a required setting, although DECISIONS (2026-10-07) says that secret is dropped. It is missing from `.env.example`. | `config.py:21` | Hosted backend will crash at start without it. Needs a decision (§6). |
| There is **no** `backend/Dockerfile` and **no** `frontend/Dockerfile`, though `stack.md` lists both. `infra/Dockerfile` is emulators only. `docker-compose.yml` has only the `emulators` service. | repo | T6 needs two new Dockerfiles (outside `infra/`: needs your OK, §5). |
| `infra/entrypoint.sh` imports `/data/export` if present, runs project `demo-study-companion`. | `infra/entrypoint.sh` | A seed export can be baked into the emulator image with no script change beyond a COPY. |
| `stack.md` says judges' `docker compose up` needs "only a Gemini API key". The brief says "zero external API dependencies". | `stack.md:82` vs brief | **Conflict, flagged for you to decide** (§5.4). |
| Firebase Storage's default bucket now requires the Blaze plan, so it is not available on Spark. | external ⚠ | Confirms Option A/B are the only paths. |

## 2. Backend container hosting

### Candidates

| Host | Free tier (⚠ verify) | Card needed? | Idle behaviour | Verdict |
| --- | --- | --- | --- | --- |
| **Render Free web service** | 512 MB RAM, 0.1 CPU, 750 h/month, Docker supported | No | Sleeps after ~15 min idle, cold start ~30-60 s | **Recommended** |
| Koyeb Free | 1 small instance, 512 MB | Possibly (identity check) ⚠ | Scales to zero | Fallback only if Render fails |
| Fly.io | No perpetual free tier for new accounts; card required | **Yes** | n/a | **Rejected** (violates zero-card rule) |

**Recommendation: Render Free, Docker runtime.** Reason: no card, Dockerfile deploy from the GitHub repo, one-click env/secret-file UI. Main alternative: Koyeb, which is similar but its free tier has changed repeatedly.

### Build compatibility (new `backend/Dockerfile`)

- Base `python:3.12-slim`; install `uv`, then `uv sync --frozen --no-dev` (excludes the `laptop` group: no faster-whisper/ffmpeg, matching `stack.md`).
- **Bake the fastembed model into the image at build time** (`bge-small-en-v1.5`, ~130 MB) so first request does not download it. Set `FASTEMBED_CACHE_PATH` to a path inside the image.
- 512 MB RAM is tight: run `uvicorn` with **one worker**, no `--reload`. Verify peak RSS locally with `docker stats` before deploying. If it exceeds ~450 MB, the fallback is Koyeb or trimming (not loading the embedder until first query).
- No LibreOffice in the image (PPTX/DOCX upload is not built).

### Port binding

Render injects `PORT` (default 10000). Start command must bind `0.0.0.0` and read it:
`uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}` (shell-form `CMD`). Health check path: an existing lightweight route (confirm `/health` exists; if not, `/openapi.json` works with no code change).

### Cold-start mitigation (all free, no code changes)

1. Frontend login page fires one `GET {API}/openapi.json` on load (the user is typing credentials for 10+ s). This is a frontend change, so it is a lane-B request, not mine.
2. Before every demo: open the API URL ~2 minutes ahead (written in the demo checklist).
3. Optional: an external pinger (UptimeRobot free, 5-min interval) to keep the service awake. ⚠ Render's 750 h/month covers one always-on service exactly; do not run two.
4. Judges use `docker compose up` (§5), which has no cold start. The hosted URL is a convenience, not the primary judging path.

## 3. File storage under Spark

### Option A: Cloudflare R2

- 10 GB free, zero egress ⚠. **Needs a card or PayPal on signup** in many regions ⚠; that conflicts with the zero-card-risk rule, so this must be confirmed before choosing it.
- Needs `boto3` (new dependency, must be approved) and an S3 branch in `storage.py`; Range reads map to `get_object(Range="bytes=a-b")`, which is a natural fit.
- Adds 3 secrets (account ID, access key, secret) and a second external service to keep alive.

### Option B: PDFs bundled in the backend image (read-only)

- Demo course PDFs live in `backend/demo_assets/notebooks/{nb}/...`, copied into the image. A file backend in `storage.py` serves `get_object_size` / `stream_byte_range` with `os.stat` and `open().seek().read()`. No new dependency, no external service, no secrets, and it is the same code path judges get in Docker Compose.
- Limits: the set is read-only and fixed at build time. User uploads in production do not persist (Render's disk is ephemeral). That is acceptable for a demo of one pre-ingested course; say so in the README.
- Image size: the PDFs must be small enough to commit or be fetched at build time. **Check the size of the demo PDFs (and their licence) first.**

### Recommendation: **Option B**

Reason: zero cost risk, zero new dependencies, zero secrets, identical behaviour in Compose and hosted. Main alternative: Option A if the demo needs live uploads; revisit only if R2 signup is card-free for you.

### What `storage.py` must change (needs your explicit approval, since the brief says not to edit `backend/app/`)

Keep the six public function names and signatures unchanged, so `sources.py`, `ingest_course.py` and the tests do not change. Add one env switch, `STORAGE_BACKEND=gcs|local` (default `gcs`, so current behaviour and the `demo-` safety check are untouched). With `local`:

- reads come from `LOCAL_ASSETS_DIR` (default `/app/demo_assets`);
- `upload_bytes` / `delete_prefix` raise a clear "read-only deployment" error (the upload endpoint then returns an error instead of crashing obscurely);
- `get_storage_bucket` is never called.

Estimated size: ~50 lines, one file, plus a small test using a temp dir. I will not write it until you approve.

**Seeding flow:** run ingestion on the laptop against the real project (`study-companion-d049d`, using the service-account file) so Firestore holds the chunks and `viewer_path` values; copy the same PDFs into `backend/demo_assets/` with the identical relative paths. In ingestion, the `upload_bytes` call will fail against Spark's missing bucket, so ingestion for production needs `STORAGE_BACKEND=local` pointing at the assets folder with a write-enabled mode, or the Firestore-only step run separately. **Open question for the ingest lane:** which of the two; I'd suggest a write-enabled `local` mode when `LOCAL_ASSETS_DIR` is explicitly set by a script.

## 4. Production environment matrix

### Hosted backend (Render)

| Variable | Value | Secret? | Notes |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | your key | **Yes** | Render env var, never committed |
| `GEMINI_MODEL` | `gemini-3.8-flash` | No | per stack.md |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.5-flash,gemini-3.1-flash-lite` | No | |
| `FIREBASE_PROJECT_ID` | `study-companion-d049d` | No | Must not start with `demo-` |
| `FIREBASE_STORAGE_BUCKET` | `unused-placeholder` | No | Required by `config.py`, never read when `STORAGE_BACKEND=local` |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/etc/secrets/firebase-sa.json` | Path only | The JSON is uploaded as a **Render Secret File**; `config.py` already accepts a path, so no code change |
| `STORAGE_BACKEND` | `local` | No | New, pending §3 approval |
| `LOCAL_ASSETS_DIR` | `/app/demo_assets` | No | New, pending §3 approval |
| `CORS_ORIGINS` | `https://<vercel-prod-domain>` (add preview domains only if needed) | No | No trailing slash |
| `JOBS_RUNNER_SECRET` | any random string | Yes | Only because `config.py` still requires it; see §6 |
| `FASTEMBED_CACHE_PATH` | `/app/.fastembed` | No | Matches the build-time download |
| **Must be absent** | `FIRESTORE_EMULATOR_HOST`, `FIREBASE_AUTH_EMULATOR_HOST`, `STORAGE_EMULATOR_HOST` | | If set, the Admin SDK talks to nothing |

Also: Firebase console → Authentication → Settings → **Authorized domains**: add the Vercel domain.

### Vercel frontend (Hobby)

| Variable | Value |
| --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `https://<render-service>.onrender.com` |
| `NEXT_PUBLIC_FIREBASE_API_KEY` | web API key from the Firebase console (public identifier, not a secret) |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | `study-companion-d049d.firebaseapp.com` |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | `study-companion-d049d` |
| `NEXT_PUBLIC_FIREBASE_APP_ID` | web app ID from the console |
| `NEXT_PUBLIC_USE_EMULATORS` | `false` |

`NEXT_PUBLIC_*` values are baked in at build time, so changing one needs a redeploy.

## 5. T6: `docker compose up` for judges

### 5.1 Services

| Service | Image | Ports | Depends on |
| --- | --- | --- | --- |
| `emulators` | existing `infra/Dockerfile`, **plus** a baked seed export | 4000, 8080, 9099, 9199 (others unchanged) | none |
| `backend` | new `backend/Dockerfile` (same one as hosted) | 8000 | `emulators` healthy |
| `frontend` | new `frontend/Dockerfile` (multi-stage `next build`, `next start`) | 3000 | `backend` |

### 5.2 Backend wiring in Compose

`FIREBASE_PROJECT_ID=demo-study-companion`, `FIRESTORE_EMULATOR_HOST=emulators:8080`, `FIREBASE_AUTH_EMULATOR_HOST=emulators:9099`, `STORAGE_BACKEND=local`, `CORS_ORIGINS=http://localhost:3000`, `GEMINI_*` from the judge's `.env`. Use service name `emulators`, not `127.0.0.1`, inside the network. Do **not** set `GOOGLE_APPLICATION_CREDENTIALS`.

For `STORAGE_BACKEND=local`, the `demo-` safety check in `get_storage_client` is not reached, which is correct because no GCS client is built.

### 5.3 Frontend wiring: a catch

`NEXT_PUBLIC_*` is build-time. The browser (not the container) calls the API and the Auth emulator, so the values must be host-visible: `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`, `NEXT_PUBLIC_USE_EMULATORS=true`, emulator at `127.0.0.1:9099`. Pass them as Dockerfile `ARG`s from Compose `build.args`. Never use service names here.

### 5.4 "Pre-warmed responses, zero external API dependencies": conflict to resolve

Two things are needed for this and one cannot be done inside `infra/`:

1. **Pre-warmed data (doable in `infra/`):** generate an emulator export from a finished ingest + sample chats/quizzes, commit it as `infra/seed/`, `COPY` it to `/data/export` in the emulator image. `entrypoint.sh` already imports it. Caveat: the Compose volume `emulator_data` will shadow the baked export on a second run with a stale volume; use `docker compose down -v` in the README, or copy the seed at start when the volume is empty.
2. **No Gemini calls (needs an app change):** `stack.md` states that the LLM cache lives behind `backend/app/llm/`. If cached model outputs are in Firestore and included in the seed, repeat demo questions are served with no network. But a **new** question still calls Gemini. Options:
   - (a) Keep `stack.md`'s position: judges need a Gemini key for new questions; seeded questions work offline. **Recommended**, no code change.
   - (b) Add a `LLM_OFFLINE=true` mode that fails with a friendly "not in demo cache" message on cache miss. A small change in `llm/`, which is not my lane; needs approval.
   - Embeddings (`fastembed`) are local ONNX, so they are not an external dependency, provided the model is baked into the image.

Proposed `DECISIONS.md` line for (a): *2026-10-12: Judges' `docker compose up` serves seeded questions offline from the cached Firestore seed; new questions need a Gemini key. Reason: an offline LLM mode would be a code path we cannot test against real answer quality.*

### 5.5 Files this would touch (all need approval, since only `infra/` and root configs are in scope)

| File | Change |
| --- | --- |
| `docker-compose.yml` | add `backend`, `frontend`; add `env_file` and healthcheck dependencies |
| `backend/Dockerfile` | new |
| `frontend/Dockerfile` | new (folder outside the brief; ask) |
| `infra/Dockerfile` | add `COPY seed /data/export` |
| `infra/seed/` | new, from an emulator export |
| `.dockerignore` files | new, keep `.env` and key files out of images |
| `.env.example` | add `STORAGE_BACKEND`, `LOCAL_ASSETS_DIR`, `JOBS_RUNNER_SECRET` names (no values) |

## 6. Risks and decisions needed from Shreyas

1. **Approve editing `backend/app/storage.py`** (~50 lines, §3). Without it, neither storage option works. This is the single blocker.
2. **Confirm Option B**, after checking the demo PDFs' total size and whether they can be committed or shipped in an image.
3. **`JOBS_RUNNER_SECRET`**: DECISIONS says it was dropped but `config.py` still requires it. Either remove the field (a small backend fix) or set a dummy value. I suggest removing it and opening a separate bug.
4. **Allow `frontend/Dockerfile`** to be created (outside the folders named in the brief).
5. **Choose 5.4 (a) or (b).**
6. **Render memory:** measure the backend's RSS in Docker first; 512 MB may not be enough with fastembed loaded.
7. **Vendor terms (⚠):** re-check Render free limits, Koyeb card requirements and R2 signup requirements today. All were written from memory.
8. **Cost:** the hosted backend still calls Gemini on free-tier quota; the model chain already handles 429s. Nothing here adds Firestore reads in loops.

## 7. Suggested order once approved

1. `storage.py` local backend + test (backend lane).
2. `backend/Dockerfile`; run it locally with `docker run` and check `docker stats`.
3. Seed export and `infra/Dockerfile` change; extend `docker-compose.yml`; test `docker compose up` from a clean `down -v`.
4. Deploy to Render with the §4 matrix; add the Vercel domain to Firebase Auth authorized domains; set Vercel env vars; smoke-test login, ask, and PDF Range requests (`curl -H "Range: bytes=0-99" -i .../file` should return `206`).
