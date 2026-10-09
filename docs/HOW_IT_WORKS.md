# How it works

Short notes on each feature's data flow, drafted with AI assistance (Claude Code) after the feature passes its acceptance checks. They double as judge-question prep.

Template for each feature:

---

## Feature name

**What it does:**

**Data flow:** (request → which functions → which Firestore collections → what comes back)

**Main files:**

**A judge might ask… / my answer:**

---

## T1 · Emulators

**What it does:** Runs the Firebase Authentication, Firestore and Storage emulators, plus the Emulator UI, in one Docker container on the laptop, so all development and testing happens locally with no cloud costs. It uses the project ID `demo-study-companion`; the Firebase CLI treats any `demo-` project as local-only, so no credentials are needed and nothing can reach the real Firebase project.

**Data flow:** `docker compose up emulators` → `docker-compose.yml` builds the image from `infra/Dockerfile` and mounts the named volume `emulator_data` at `/data` → `infra/entrypoint.sh` imports `/data/export` if `firebase-export-metadata.json` exists, then starts the emulators with `exec` → the emulators read their ports and rules from `infra/firebase.json` → on a graceful stop (Ctrl+C or `docker compose stop`) the CLI exports all data to `/data/export`, ready for the next start. No app collections exist yet; the UI is at http://localhost:4000.

**Main files:** `docker-compose.yml`, `infra/Dockerfile`, `infra/entrypoint.sh`, `infra/firebase.json`, `infra/firestore.rules`, `infra/storage.rules`, `infra/firestore.indexes.json`.

**A judge might ask… / my answer:**
- *Why emulators instead of the real Firebase?* They're free, fast and offline, they can't cause surprise cloud bills, and the same app code switches to the real project in deployment just by changing environment variables.
- *Does local data survive a restart?* Yes. A named Docker volume holds it, the CLI exports on shutdown and imports on start, `init: true` and `exec` make sure the stop signal reaches the CLI, and a 60-second grace period lets the export finish. `docker compose down -v` deletes it.
- *What went wrong while building it?* Exporting straight to `/data` failed with `EBUSY: resource busy or locked, rmdir '/data'`. The CLI deletes and recreates its export folder before writing, and `/data` is the volume's mount point, which can't be deleted from inside the container. Exporting to the subfolder `/data/export` fixed it.
- *Why Java in a Node image?* firebase-tools is a Node program, but the Firestore emulator runs on Java 21, so the image is Node 22 with a Java 21 runtime copied in. Versions are pinned and the emulators are downloaded at build time, so startup works offline.
- *Who can read the data?* The rules deny all access from browsers and apps. Only the backend reads and writes, through the Firebase Admin SDK, which bypasses rules.

## T3 · Sign-in check and /v1/me

**What it does:** Every request (except /v1/health) must carry a Firebase ID token. The backend checks it, works out who the user is and whether they are a guest, and `GET /v1/me` returns their profile, creating it on first visit. `PATCH /v1/me` changes their settings (Study Coach on or off, custom instructions).

**Data flow:** browser signs in with Firebase Auth → sends `Authorization: Bearer <ID token>` → `app/auth.py` rejects a missing or malformed header with 401, then verifies the token with firebase-admin (against the Auth emulator locally) → gets the uid, the email, and `is_guest` (the sign-in provider is "anonymous") → `app/api/me.py` reads `users/{uid}` (path from `app/db/paths.py`) → if missing, creates it with `study_coach: null` and a server timestamp → returns the User. PATCH updates only the fields sent.

**Main files:** `app/auth.py`, `app/db/client.py`, `app/db/paths.py`, `app/models/user.py`, `app/api/me.py`, `app/main.py`, `tests/test_me.py`.

**A judge might ask… / my answer:**
- *How do you know who the user is?* The frontend never sends a user ID. It sends a Firebase ID token, a signed proof of identity, and the backend verifies it on every request. Guests use Firebase anonymous sign-in, so they get a real uid too.
- *What if two first requests arrive at once?* The profile is created with Firestore's `create()`, which fails if the document already exists. The losing request catches that error and simply reads the existing document, so there's never a duplicate or an overwritten setting.
- *Which errors become 401 and which become 500?* Only real token problems (missing, malformed, invalid, expired or revoked) return 401 "unauthenticated". Configuration mistakes are not caught and surface as 500, so a broken setup never looks like "you're not logged in".
- *How can local runs never touch real data?* The project ID starts with `demo-`, and `app/db/client.py` refuses to start if the emulator addresses are missing. The tests check the same thing and delete only the documents and users they created.
- *Why are the endpoints named me_get and me_patch?* Handlers are named by their verb, and the OpenAPI operation ID is `{tag}_{verb}`. Those IDs become the function and type names in the frontend's generated TypeScript, and a test pins the exact IDs.
## C1 · Notebooks API

**What it does:** Lets a signed-in user (email or guest) create notebooks, list them and open one. Every notebook route goes through two shared access checks: "readable" (you own it, or it's the demo notebook) and "owned" (you own it). Someone else's notebook looks exactly like a missing one: 404 "not_found". Trying to change the demo notebook you can read but don't own gives 403 "forbidden".

**Data flow:**
- `POST /v1/notebooks {name}` → `app/auth.py` verifies the Firebase ID token and gives the route a `CurrentUser` → `NotebookCreate` strips the name and checks it is 1–100 characters (else 422 "invalid") → `app/db/notebooks.py` creates `notebooks/{auto-id}` with `ref.create()`: owner_uid = you, is_demo false, status "empty", an empty sources_summary, zero counts and a server timestamp → the document is read back so `created_at` is real → 201 with the Notebook.
- `GET /v1/notebooks?limit=&cursor=` → the cursor (if any) must be a valid ID of one of your notebooks, else 422 → Firestore query `owner_uid == you`, newest first, `limit + 1` documents to see whether another page exists → the demo is removed from that list and, on the first page only, put in front if it exists → `{items, next_cursor}`, where `next_cursor` is the last notebook's ID, or null on the last page.
- `GET /v1/notebooks/{nb}` → `app/api/access.py` `get_readable_notebook` checks the ID with a full-string match (letters, digits, `_`, `-`, up to 128 characters), reads `notebooks/{nb}` through `app/db/notebooks.py`, and returns it if you own it or it's the demo; otherwise 404.

**Main files:** `backend/app/models/notebook.py`, `backend/app/db/notebooks.py`, `backend/app/db/paths.py`, `backend/app/api/access.py`, `backend/app/api/notebooks.py`, `backend/app/auth.py`, `backend/app/main.py`, `backend/tests/conftest.py`, `backend/tests/test_notebooks.py`, `infra/firestore.indexes.json`.

**A judge might ask… / my answer:**
- *Why 404 instead of 403 for someone else's notebook?* A 403 would confirm the notebook exists. Returning the same 404 as for a missing notebook leaks nothing. 403 is only used when you can already see the notebook (the demo) but aren't allowed to change it.
- *How do judges share the demo notebook without seeing each other's work?* The demo's content is readable by everyone, but each user's chats, quizzes and mastery live under their own `members/{uid}` subtree (built in later slices), so nothing is copied and nobody overwrites anyone else.
- *Why cursors instead of page numbers?* Firestore charges for every document an offset skips, and page numbers shift when a new notebook is added. A cursor says "continue after this notebook", which is cheap and stays correct.
- *Why add an index file entry if everything works locally?* The emulator doesn't enforce indexes, but production Firestore rejects a query that filters on one field and sorts on another without a composite index. Adding it now means the list doesn't break on the first deploy.
- *Can a user make their own notebook a demo?* No. The create request accepts only `name`; sending `is_demo` is rejected with 422. Only the ingestion script on the laptop writes the demo notebook.
- *Why check IDs before reading Firestore, and why a full-string match?* Firestore raises an exception on some reserved IDs, which would turn into a 500, so bad IDs get a clean 404 first. A plain `^…$` regex would also accept an ID ending in a newline, because `$` matches just before a final `\n`; a full-string match closes that gap, and a test sends `abc%0A` to prove it.
- *Do the tests wipe your local data?* No. Each test deletes only what it created and saves and restores the demo notebook if one exists, so the emulator's dev data, and later the ingested demo course, survive a test run.
## T4 · Frontend skeleton

**What it does:** A Next.js (TypeScript, App Router) app in `frontend/` that is the base for every screen. Opening `/` redirects to `/login`, which is a placeholder card, and `/notebooks` is a placeholder page. Behind them sit three pieces of plumbing: a Firebase sign-in helper (Auth only), the one function that talks to the backend, and TypeScript types generated from the backend's own API description. There is no real sign-in or data yet; that arrives in T5.

**Data flow:** Opening `http://localhost:3000/` → `app/page.tsx` redirects on the server to `/login` → `app/layout.tsx` wraps the page in `Providers` (one TanStack Query client) and loads the font. Firebase is not touched on these pages.
- `lib/firebase.ts`: `getFirebaseAuth()` runs only in the browser. It reads the six `NEXT_PUBLIC_*` variables, reuses an existing Firebase app if there is one, and, when `NEXT_PUBLIC_USE_EMULATORS` is `true`, connects to the Auth emulator at `http://127.0.0.1:9099` only if `auth.emulatorConfig` is still null. That check makes the connection happen exactly once, even when hot reload re-runs the module.
- A future call to `apiFetch` in `lib/api.ts`: builds the URL from `NEXT_PUBLIC_API_BASE_URL` → waits for `authStateReady()`, so a reloaded session is restored → adds `Authorization: Bearer <ID token>` if someone is signed in (no header if not) → sends JSON, or `FormData` untouched → on success returns the parsed JSON (`204` returns nothing). On failure it reads the backend's error envelope `{"error": {"code", "message"}}` and throws an `ApiError(code, message, status)`, keeping `retry_after_s` for quota errors. A non-JSON error body (for example an HTML 502 page) becomes `ApiError("http_error", "HTTP <status>", status)`, and an unreachable backend becomes `ApiError("network_error", …, 0)`.
- `npm run gen:api`: `openapi-typescript` downloads `http://127.0.0.1:8000/openapi.json` and writes `lib/api-types.ts`. Every operation is keyed by its operation ID (`health_get`, `me_get`, `me_patch`), so those IDs become the names used in frontend code. Renaming one in the backend renames it in the frontend.
- `app/providers.tsx`: one query client for the whole app. It does not retry 4xx errors, retries other failures up to twice, and does not refetch when the window regains focus.

**Main files:**
- `app/layout.tsx`: font, page title, `Providers`.
- `app/page.tsx`: redirect to `/login`.
- `app/login/page.tsx`, `app/notebooks/page.tsx`: placeholders.
- `app/providers.tsx`: the TanStack Query provider.
- `lib/firebase.ts`: Firebase Auth only, with the emulator connection.
- `lib/api.ts`: `apiFetch` and `ApiError`, the only file that calls the backend.
- `lib/api-types.ts`: generated, never edited by hand, committed to the repo.
- `components/ui/*`, `lib/utils.ts`: shadcn button, card, input and label, plus the `cn` helper.
- `frontend/.env.local` (git-ignored): the six variable names, with local values.

**A judge might ask… / my answer:**
- *Why generate the API types instead of writing them?* The backend's schema is the single source of truth, so the two sides cannot drift apart. If a field changes in the backend, the frontend fails to compile instead of failing at runtime.
- *Why does only `lib/api.ts` call the backend?* Authentication headers, error handling and the base URL live in one place, and the frontend holds no business logic. A check enforces it: `fetch(` appears in no other file.
- *Why wait for `authStateReady()`?* After a page reload Firebase restores the session asynchronously. Without the wait, the first request would go out with no token and get a 401.
- *Is the Firebase config a secret?* No. It only identifies the project. Access is protected by the backend's token check and by security rules that deny all client access.
- *How do you run it without a cloud sign-in?* With `NEXT_PUBLIC_USE_EMULATORS=true` the app signs in against the local Auth emulator, under the `demo-` project ID, so nothing reaches the real project.
- *What went wrong while building it?* The agent installed an unrelated npm package named `cn`, mistaking shadcn's `cn` helper function for a package, and left `clsx` and `tailwind-merge` (which the helper needs) as indirect dependencies only. The dependency check caught it before the commit. The package was removed and the two real ones added directly.
- *Known limits:* a successful response with a non-JSON body makes `apiFetch` throw, and a cancelled request is reported as `network_error`. The emulator address is hard-coded to `127.0.0.1:9099`. `/notebooks` has no sign-in guard yet (T5). The font is fetched from Google at build time, so a build needs network access.
## T5 · Sign-in and the /v1/me page

**What it does:** Students can create an account with email and password, sign in, or continue as a guest; all three land on `/notebooks`. That page asks the backend "who am I?" (`GET /v1/me`) and shows the email (or "Guest"), whether the account is a guest, and the Study Coach setting ("not asked yet", "on" or "off"). A Sign out button returns to `/login`, and `/notebooks` sends signed-out visitors back to `/login`.

**Data flow:** Login form → the browser checks the fields (`reportValidity`) → `AuthProvider` calls Firebase Auth (the emulator locally): `createUserWithEmailAndPassword`, `signInWithEmailAndPassword` or `signInAnonymously` → `onAuthStateChanged` stores the user and sets `loading` to false → an effect on `/login` sees a signed-in user and calls `router.replace("/notebooks")` → `/notebooks` runs a TanStack Query keyed `["me", uid]`, enabled only once a user exists → `getMe()` in `lib/api.ts` waits for `authStateReady()`, gets the ID token and sends `Authorization: Bearer <token>` → the backend verifies the token, creates `users/{uid}` on first sight, and returns email, `is_guest` and `study_coach` → the page renders them.
- **Guest:** a real Firebase user with no email; the backend returns `is_guest: true`.
- **Reload:** Firebase restores the session asynchronously. Until it does, `loading` stays true and both pages show "Loading…", so nobody is bounced to `/login` by mistake.
- **Sign-out:** Firebase sign-out, then `queryClient.clear()`, so the next person never sees cached data from the previous one.
- **Types:** `getMe()` and `patchMe()` take their types from `lib/api-types.ts` by operation ID (`me_get`, `me_patch`), never written by hand. A missing `study_coach` is treated like null and shows "not asked yet".

**Main files:**
- `components/auth-provider.tsx`: holds `user` and `loading`, plus sign in, create account, guest and sign out.
- `lib/auth-errors.ts`: turns Firebase error codes into plain-English messages.
- `lib/api.ts`: the only `fetch`; attaches the token, turns errors into `ApiError` (including `aborted` for cancelled requests), and has `getMe()` and `patchMe()`.
- `app/providers.tsx`: the query client, with `AuthProvider` inside it.
- `app/login/page.tsx`: the form and the redirect effect.
- `app/notebooks/page.tsx`: the guarded page that shows `/v1/me`.

**A judge might ask… / my answer:**
- *Why does the frontend never read Firestore itself?* All data goes through the backend with a verified token, and the security rules deny all client access, so there is one place that enforces who sees what.
- *How do you stop one user seeing another's data?* The backend uses the uid from the verified token, the query cache is keyed by uid, and it is cleared on sign-out.
- *Why offer a guest option?* Judges can try the app without signing up. A guest is a real Firebase user, so it goes through exactly the same backend path. The trade-off: signing out of a guest account loses it.
- *What if the token expires?* `getIdToken()` refreshes it automatically before each request.
- *What went wrong while building it?* The plan review caught two bugs before any code was written. First, the login buttons redirected straight after sign-in, before the app knew about the new user, so `/notebooks` would have seen "no user" and bounced back to `/login`. Now only effects redirect, and only after auth has finished loading. Second, `study_coach` is optional in the generated type, so a missing value would have shown nothing; it now reads as "not asked yet".
- *How was it tested?* Lint and build pass. A manual walkthrough with React Strict Mode on (account creation, reload, sign-out guard, wrong password, guest, empty fields) showed no console errors, and the Firestore emulator showed one `users/{uid}` document per account.
## C2 · PDF upload and indexing

**What it does:** Lets a notebook's owner upload a PDF. The backend stores the original in Cloud Storage, extracts the text page by page with PyMuPDF, splits each page into chunks of at most 400 tokens that never cross a page, turns each chunk into 384 numbers (an embedding) with bge-small-en-v1.5 running locally, and saves the chunks and their embeddings in Firestore so they can be searched by meaning. The viewer endpoint streams the PDF back with Range support, so the browser's PDF viewer can fetch just the bytes it needs. In checkpoint 1 all of this happens inside the upload request; S4 moves it into background jobs. The 165-page demo textbook becomes 316 chunks in about 70 seconds, almost all of it embedding.

**Data flow:**
- `POST /v1/notebooks/{nb}/sources` (multipart: file, role) → `get_owned_notebook` (404 if you can't see it, 403 for the demo) → the upload is read up to 30 MB and must contain `%PDF-` in its first 1024 bytes (else 422, nothing written) → transaction 1: next `ref_n`, a new `sources/{id}` with status "processing", a `sources_summary` entry, notebook status "processing" → `app/storage.py` uploads `notebooks/{nb}/sources/{src}/original.pdf` → `app/ingestion/pdf.py` extracts and chunks the text page by page → `app/embeddings.py` embeds the chunks → `chunks/{source_id}-00000…` are written in batches of up to 500 → transaction 2: source "ready" with `page_count`, summary updated, `counts.chunks` increased, notebook status recomputed → 202 with the Source. If anything fails after transaction 1, a third transaction marks the source "failed" with a short error, its partial chunks are deleted, and the original file stays for a later retry.
- `GET /v1/notebooks/{nb}/sources` and `GET …/sources/{src}` → `get_readable_notebook` → the sources in `ref_n` order, or one source.
- `GET …/sources/{src}/file` → `get_readable_notebook` → `app/storage.py` streams the PDF in pieces of about 1 MB (200), or reads just the requested bytes for a `Range` header (206 with `Content-Range`), or answers 416 for a range that starts past the end. A Range header it can't parse is ignored, as the HTTP standard says, and the whole file is sent.

**Main files:** `backend/app/storage.py`, `backend/app/embeddings.py`, `backend/app/ingestion/pdf.py`, `backend/app/models/source.py`, `backend/app/db/sources.py`, `backend/app/db/paths.py`, `backend/app/api/sources.py`, `backend/app/config.py`, `backend/app/main.py`, `backend/tests/`.

**A judge might ask… / my answer:**
- *Why do chunks never cross a page?* Every answer cites the page a chunk came from. A chunk that started on page 40 and ended on page 41 couldn't be cited to one exact page, so pages are hard boundaries.
- *Why at most 400 tokens per chunk?* The embedding model reads at most 512 tokens and silently cuts off the rest. Counting with the model's own tokenizer and stopping at 400 keeps every chunk fully read, with room to spare.
- *Why run embeddings locally instead of through an API?* bge-small-en-v1.5 runs on a laptop CPU through ONNX, costs nothing and never uses Gemini quota. The same model embeds the questions later, so questions and passages live in the same "meaning space".
- *What is the query instruction?* BGE models were trained to see search queries starting with "Represent this sentence for searching relevant passages: ". Adding it to questions (not to passages) improves how well a short question finds the right paragraph.
- *Why Range requests?* A textbook PDF can be many megabytes. With Range support, the viewer downloads the first bytes and then only the pages you open, so a citation opens quickly instead of waiting for the whole file.
- *How do two uploads at once avoid getting the same @-number?* The next `ref_n` is chosen inside a Firestore transaction. If two uploads collide, Firestore retries one of them, and it sees the other's number. A test runs two uploads in parallel threads and checks they get 1 and 2.
- *What happens with a scanned or password-protected PDF?* The upload finishes as "failed" with a clear message, such as "This PDF has no extractable text (it may be scanned)." Nothing half-indexed is left behind.
- *Does vector search work without the cloud?* Yes. A test writes chunks with known vectors to the local Firestore emulator and checks that `find_nearest` with cosine distance returns them in the right order.
- *What went wrong while building it?* The build review found that one very long run of punctuation with no spaces (like a long URL or formula) produced pieces of 401–402 tokens and silently dropped about 300 characters, because the token offsets included the tokenizer's start and end markers. Splitting without those markers fixed it, and a test now checks that a 1,500-character run keeps every character, including its last word. The review also caught that marking an upload "failed" rewrote the notebook's source list outside a transaction, so a parallel upload could lose its entry; that write is now transactional too.

## C4a · Notebooks, upload and the sources list

**What it does:** The notebooks page lists your notebooks (the demo notebook first, marked "Demo") and lets you create a new one, which opens straight away. A notebook's own page shows its name, status and sources, and lets its owner upload a PDF. Upload takes up to about two minutes, because the backend reads, splits and indexes the PDF inside the request, so the page shows a processing message, disables the controls, and then reports "ready" or "failed" with a short reason. Visitors who don't own the notebook, and everyone on the demo notebook, can see its sources but can't upload.

**Data flow:**
- *List:* `/notebooks` → `useInfiniteQuery(["notebooks", uid])` → `listNotebooks()` → `GET /v1/notebooks?limit=20` → items plus `next_cursor`; "Load more" asks for the next page with that cursor. The backend already puts the demo notebook first, so the page doesn't re-sort.
- *Create:* name (trimmed, not empty) → `createNotebook()` → `POST /v1/notebooks` → the list is invalidated, then `router.push` to `/notebooks/<id>`, so the browser's Back button returns to the list.
- *Notebook page:* `useParams<{ id: string }>()` → `getNotebook()` → `GET /v1/notebooks/{id}`. A 404 shows "Notebook not found.", whether the notebook doesn't exist or belongs to someone else, so the app never reveals which. The sources query (`listSources()`, `GET /v1/notebooks/{id}/sources`) only starts once the notebook has loaded, so a missing notebook never causes a second 404.
- *Upload:* the browser checks first: a file chosen, a PDF, at most 30 MB. Only then does `uploadSource()` send `POST /v1/notebooks/{id}/sources` as multipart (`file`, `role: "content"`) with a 3-minute timeout. After every attempt, success or not, the sources, the notebook and the notebook list are refreshed, because a request that timed out in the browser may still finish on the server.
- *Guard:* `use-require-user` waits for Firebase to finish loading, then sends signed-out visitors to `/login`. The account lines and Sign out moved unchanged into a shared `AccountBar`.

**Main files:**
- `frontend/lib/api.ts`: `listNotebooks`, `createNotebook`, `getNotebook`, `listSources`, `uploadSource`, typed by operation ID; timeouts become `ApiError("timeout")` with a message the caller chooses.
- `frontend/lib/use-require-user.ts`: the sign-in guard shared by every signed-in page.
- `frontend/components/account-bar.tsx`: account lines and Sign out (disabled while signing out).
- `frontend/app/notebooks/page.tsx`: the create form and the paged notebook list.
- `frontend/app/notebooks/[id]/page.tsx`: the notebook page, upload form and sources list.

**A judge might ask… / my answer:**
- *Why does upload take so long, and what if it times out?* Checkpoint 1 processes the PDF inside the request: reading every page, splitting it into chunks and computing embeddings locally. That takes about 70 seconds for the 165-page textbook. The browser waits up to 3 minutes; if it gives up, the server may still finish, so the page refreshes the sources list afterwards and the source appears as ready. In S4 this moves to a background job with a progress bar.
- *Why check the file in the browser if the backend checks it too?* The backend is the real gatekeeper, but checking first means a wrong file fails instantly instead of after a slow upload. The messages are word for word the backend's, so the student sees the same text either way.
- *Can another user see or upload to my notebook?* No. The backend answers 404 for notebooks you can't see and 403 for uploads to notebooks you don't own; the page hides the upload form for non-owners and the demo notebook, and every cached list is keyed by your user ID.
- *Why "Notebook not found." for someone else's notebook, not "Access denied"?* Saying "denied" would confirm the notebook exists. "Not found" reveals nothing.
- *What went wrong while building it?* The plan review merged two overlapping review files into one list of eleven fixes, among them a "Notebook not found." message that flashed before loading finished, a sources request firing for notebooks that don't exist, and upload results not refreshing after a timeout. The final verification found three small issues (a flash of "No sources yet.", a file-input edge case after Back and Forward, and an untyped form field name), all fixed before the walkthrough.

## C3 · Cited answers (/ask)

**What it does:** Answers a question using only the notebook's own sources, and shows exactly where each part of the answer came from. The backend finds the passages closest in meaning to the question, gives them to Gemini as numbered sources, and asks it to write paragraphs that each list the sources they rely on. Those numbers become citations that open the PDF at the right page. A question the course doesn't cover gets a single "not covered" paragraph with no citations instead of an invented answer. The response also returns the exact passages that were sent, so the evaluation harness can score the answer against them.

**Data flow:** `POST /v1/notebooks/{nb}/ask {question}` → `get_readable_notebook` (yours, or the demo) → 409 "not_ready" if no source is ready → `app/retrieval/search.py` embeds the question with the BGE query instruction and runs `find_nearest` (cosine, 40 results) on `notebooks/{nb}/chunks`, drops chunks of sources that aren't ready, and keeps the top 10 → `app/chat/` builds the prompt: the rules as the system instruction, then the 10 chunks as `<<<SOURCE n>>> … <<<END n>>>` blocks with title and page, then the question → `app/llm/` checks `llm_cache/{sha256}`; on a hit it returns the stored answer with no Gemini call and no write; on a miss it calls Gemini for JSON matching `{paragraphs: [{text, sources}]}`, retrying only server errors and timeouts, then stores the validated answer → `app/chat/` maps each source number to its chunk and builds Citations ("textbook p. 72" plus where to open it); unknown numbers and chunks without a page are dropped, and a paragraph left without citations is marked `outside_course` → the response holds `{paragraphs, context, model, latency_ms}`, where `context` is exactly the 10 chunks Gemini saw, with their text, location and score. Nothing is written except the cache entry.

**Main files:** `backend/app/llm/`, `backend/app/llm/prompts/`, `backend/app/retrieval/search.py`, `backend/app/chat/`, `backend/app/models/ask.py`, `backend/app/api/ask.py`, `backend/app/main.py`, `backend/tests/conftest.py`, `backend/tests/`, `docs/architecture/api-contract.md` (the new 503 row).

**A judge might ask… / my answer:**
- *How do you stop the model from making things up?* It only sees the 10 retrieved passages, the rules say to answer only from them, and every paragraph has to name its sources. A paragraph with no valid source is never shown as course content: it goes to the "Beyond your course" box, or, by default, the model must say the material doesn't cover the question.
- *Does it actually cite the right page?* Asked "What is conditional probability?", it answered with the textbook's definition and the formula P(F|E) = P(F∩E)/P(E), citing physical pages 70 and 72. I checked both by hand: page 70 opens §4.1 with the definition, and page 72 states the formula. The page in between, which holds the derivation, wasn't cited, which is right for a "what is" question.
- *Why number the sources instead of passing their IDs?* A model copying a 20-character ID can garble it; copying "3" is reliable. The backend maps numbers back to chunks and drops any number it didn't send, so a citation can never point to something the model wasn't shown.
- *Can a PDF hijack the answer with hidden instructions?* The rules live in the system instruction, the passages are wrapped as delimited data, and the rules say text inside them is never an instruction. That reduces the risk; it doesn't make it impossible, which is one reason every claim must cite a source you can open and check.
- *Why return the context?* The evaluation harness (RAGAS) needs the exact passages the answer was based on to measure faithfulness and relevance. Returning them from the same endpoint means the evaluation scores the same code the demo runs.
- *What does the cache do?* The same question over the same passages, with the same model and prompt, returns the stored answer instantly (113 ms in testing) and uses no quota. The key includes a hash of the full prompt, so re-ingested text under the same chunk IDs, a new prompt version or a different model never gets a stale answer.
- *What happens when the free quota runs out, or the model is overloaded?* Quota exhausted (429): no retry, because retrying would only burn more quota; the API returns `quota_exhausted` with how many seconds to wait. Overloaded or timing out: up to 3 attempts within 100 seconds, then a 503 `unavailable` with Retry-After, so the app can say "busy, try again in a minute". This happened for real during testing, when Gemini reported "high demand", and the 503 came through exactly as designed.
- *How was it tested without spending quota?* The tests replace the Gemini client with a fake for every test automatically, so an unmocked call fails instead of reaching Google. The suite passes identically with a fake API key, which proves it. The fake sits under the retry logic, so the retry tests exercise the real retry code. Only the final checks made real calls.
- *What went wrong while building it?* The plan review caught three serious bugs before any code ran: the SDK's timeout is in milliseconds, so "60" would have meant 60 ms; the tests loaded the real key, so a missed mock would have spent the daily quota on every test run; and mocking the wrong function would have skipped the retry logic, so the retry tests would have proven nothing. The first live check then found that the cache tests deleted cached answers they hadn't created, and that a Gemini outage became an unexplained 500. Both are fixed and tested.

## C5 · PDF viewer

**What it does:** Every ready source on a notebook page has an **Open** button that shows the PDF in a side panel (below the content on narrow screens, which scrolls to it). You can page through it, jump to a page number, and select and copy its text. The viewer is built so that a citation can open a source at an exact page; the ask box (C4b) uses that.

**Data flow:** Open → `openSource({sourceId, title, page})` in the viewer context → `PdfViewer` (loaded with `next/dynamic` and `ssr: false`, because pdf.js only runs in the browser) → `getSourceFileRequest()` in `lib/api.ts` builds `GET /v1/notebooks/{nb}/sources/{src}/file` and a fresh `Authorization: Bearer <token>` → react-pdf hands both to pdf.js, which fetches the PDF in pieces with HTTP Range requests (`206 Partial Content`), so page 25 appears without downloading the whole 165-page file first. The token request is a TanStack Query with no caching (`staleTime` and `gcTime` 0), keyed by user, notebook and source, and the document isn't rendered while a new token is on its way. Page numbers are the PDF's physical pages, the same numbering citations use in `open.page`.

**Main files:**
- `frontend/components/pdf-viewer.tsx`: react-pdf `Document` and `Page`, the worker set up with `new URL(…, import.meta.url)` (no CDN), toolbar, page clamping, width fitting with a `ResizeObserver`, and the error state with "Try again".
- `frontend/components/viewer-context.tsx`: `ViewerProvider` and `useViewer()`, which hold which source and page are open.
- `frontend/lib/api.ts`: `getSourceFileRequest()`, plus the shared `getApiBaseUrl()` and `getAuthToken()` helpers that `apiFetch` also uses.
- `frontend/app/notebooks/[id]/page.tsx`: the Open buttons, the side-panel layout, and `<PdfViewer key={sourceId}>`, so switching sources starts a fresh viewer.

**A judge might ask… / my answer:**
- *Why not just download the PDF and show it?* A textbook can be tens of megabytes. Range requests let the viewer fetch only the pages you look at, so jumping to a cited page is fast.
- *The rule says only `lib/api.ts` calls the backend. Doesn't pdf.js break it?* pdf.js does the network requests, but the URL and the token are built only in `lib/api.ts`, so there's still one place that decides where requests go and how they're authenticated. It's the one documented exception.
- *What happens when the sign-in token expires after an hour?* pdf.js keeps fetching pages with the token it was given, so a later page fails to load. The viewer treats document, source and page errors alike and shows "Couldn't load this PDF." with "Try again", which fetches a fresh token and reopens at the same page.
- *Why is a token never cached?* A cached token is exactly what goes stale. Every open and every retry asks Firebase for a current one, which it refreshes automatically when needed.
- *What went wrong while building it?* The plan review caught three serious bugs before any code was written: the page couldn't reach its own viewer context, switching sources would have briefly shown the old PDF, and an expired token would have failed silently as a page error. The recheck caught that a retry would briefly reuse the old token. The final verification found small page-box issues (0 and negative numbers weren't clamped, and a clamped number wasn't shown), fixed before testing.

## C4b · Ask box and citations

**What it does:** On a notebook page you type a question and get an answer built only from your sources. Each paragraph is rendered as Markdown with real maths, and ends with citation buttons such as "textbook p. 72"; clicking one opens the PDF viewer at exactly that page. Any paragraph without a source, including the reply "your course material doesn't cover this", appears in a separate "Beyond your course" box marked "Not from your sources." "Passages used" shows the ten passages the answer was based on, with their page and score. Problems show plain messages instead of failing silently: no processed source yet, the AI quota used up (with how many seconds to wait), the model busy, or a timeout.

**Data flow:** question (trimmed, up to 2,000 characters) → `ask()` in `lib/api.ts` → `POST /v1/notebooks/{nb}/ask {question, allow_outside}` with a 150-second timeout → the backend retrieves ten passages, asks Gemini and returns `{paragraphs, context, model, latency_ms}` → the answer is kept in the page's state for this visit, newest first → `components/answer.tsx` converts `\( … \)` and `\[ … \]` into `$…$` and `$$…$$` (code untouched) and renders the Markdown with react-markdown, remark-math and rehype-katex, never as raw HTML → a citation click calls `openSource({source_id, page, title})` in the viewer context, whose counter makes the viewer jump to that page and, on narrow screens, scroll into view, even when that PDF is already open.

**Main files:**
- `frontend/components/answer.tsx`: paragraphs, maths, citation buttons, the "Beyond your course" box and "Passages used".
- `frontend/app/notebooks/[id]/page.tsx`: the ask form, the request, the answers list and the scroll-to-viewer behaviour.
- `frontend/lib/api.ts`: `ask()`, typed from the `ask_post` operation.
- `frontend/components/viewer-context.tsx`: the navigation counter that lets a citation reopen a page in an already-open PDF.
- `frontend/components/ui/textarea.tsx`: the shadcn text box.

**A judge might ask… / my answer:**
- *How do I know the answer isn't made up?* Every course paragraph links to the exact page it came from, so you can check it in one click. Anything without a source is never shown as course content; it always sits in the labelled "Beyond your course" box.
- *Why is "not covered" inside the "Beyond your course" box?* The rule is simple and safe: every uncited paragraph goes there, even if the model breaks its instructions. So nothing unsourced can ever look like it came from your course.
- *Why doesn't the page retry when the AI fails?* The free quota is small. The backend already retries server errors; retrying again from the browser would only burn requests. Instead the page says what happened and how long to wait.
- *Why convert maths delimiters?* The maths renderer only understands `$…$`, and language models sometimes write `\( … \)`. Converting first means formulas always render.
- *Can an answer inject code into the page?* No. Markdown is rendered without raw HTML, and links open in a new tab with `noopener`.
- *What went wrong while building it?* The plan review caught two bugs that would have broken the page: a button property that doesn't exist in this UI kit (the build would fail), and React hooks placed after early returns (React would crash). The verification found the shadcn command had again installed an unrelated npm package called `cn`; it was removed. The live test also hit Gemini's "high demand" outage, and the page showed "The AI model is busy" exactly as designed.

---

## F1a · Study Coach question and settings

**What it does:** The first time anyone signs in, with email or as a guest, the notebooks page shows a short card, "Want Study Coach?". It explains what the coach tracks and offers "Yes, turn it on" or "No thanks". The choice is saved to the account and the card never comes back. A Settings page, linked from the account bar, lets you switch Study Coach on or off at any time and write custom instructions (up to 2,000 characters) for how the tutor should write its answers.

**Data flow:** sign-in → the account bar and the card share one `GET /v1/me` request through the TanStack Query key `["me", uid]` → on the first call the backend creates `users/{uid}` with `study_coach: null` → the card renders only while `study_coach` is null → a click calls `usePatchMe` → `patchMe()` in `lib/api.ts` → `PATCH /v1/me {study_coach: true | false}` → the backend updates `users/{uid}` and returns the whole user → `usePatchMe` writes it into `["me", uid]`, so the card disappears and the account line changes without a reload. The Settings page sends the same PATCH for the on/off button, and `{format: {custom_instructions}}` for the instructions (trimmed; an empty box saves `null`). The backend rejects more than 2,000 characters with 422 `invalid`.

**Main files:**
- `frontend/components/study-coach-prompt.tsx`: the first-launch card.
- `frontend/app/settings/page.tsx`: the Study Coach switch and the custom-instructions form.
- `frontend/lib/use-patch-me.ts`: the one hook every profile change goes through; it updates the cached user.
- `frontend/components/account-bar.tsx`: the Settings link and the "Study Coach: on / off / not asked yet" line.
- `backend/app/api/me.py` and `backend/app/models/user.py`: `GET` and `PATCH /v1/me`, and the 2,000-character limit.

**A judge might ask… / my answer:**
- *Why ask instead of tracking everyone?* Study Coach builds a picture of how well you know each topic, which is personal data, so it's opt-in. The question comes on first launch, so nobody gets tracked without choosing it, and it can be changed any time in Settings.
- *What happens if I say no?* Quizzes still work, with the right answer and a cited explanation after each question. Nothing is recorded about your mastery, and "needs work" falls back to the topics you haven't ticked.
- *Why a card and not a pop-up?* A pop-up blocks the page. The card sits at the top of the notebooks list, so you can ignore it and still open the demo notebook straight away.
- *Can custom instructions make the tutor ignore the course?* No. They change how answers are written, not where the facts come from. The backend still decides which passages the model sees and drops any citation that doesn't match one of them.
- *Can anyone else see my settings?* No. The browser never reads Firestore directly (the security rules deny all client access), and the backend only returns `users/{uid}` to the user with that uid's token.

---

## F1b · Source cards

**What it does:** Each source on a notebook page is a card showing its title, its kind (PDF, Slides, Video, PowerPoint…), "Syllabus" if it's the syllabus, and its page count or length. A source that isn't ready shows "Queued", "Processing…" or "Failed" with the reason. When a source has a licence and attribution, as every file in the demo course will once its notebook is built, the card shows them, for example "Licence: CC BY-NC-SA 4.0" followed by "MIT OpenCourseWare, 6.041…"; when it has none, the card shows nothing extra. Ready PDFs have an Open button that opens the viewer; ready lecture videos have a "Watch on YouTube" link.

**Data flow:** notebook page → `listSources()` in `lib/api.ts` → `GET /v1/notebooks/{nb}/sources` → the backend reads `notebooks/{nb}/sources` and builds each item with `SourceOut.from_stored`, which adds `licence`, `attribution` and, for videos only, a `youtube_url` made from `youtube_id` and `offset_s` → `components/source-card.tsx` renders one card per item → Open calls `openSource({sourceId, title, page: 1})` in the viewer context; a video's link uses `youtube_url` exactly as the backend built it. The demo course's licence and attribution come from `manifest.csv` when the demo notebook is built; a student's own uploads have none.

**Main files:**
- `frontend/components/source-card.tsx`: the card, kind labels, durations, status and licence block.
- `frontend/app/notebooks/[id]/page.tsx`: the grid of cards (one column while the viewer is open).
- `backend/app/models/source.py`: `SourceOut` and `from_stored`, including how `youtube_url` is built.
- `frontend/lib/api-types.ts`: regenerated after SRC1, so the frontend's types match the backend.

**A judge might ask… / my answer:**
- *Why show licences at all?* The demo course is MIT OpenCourseWare (CC BY-NC-SA 4.0) and Grinstead & Snell (GNU FDL). Both licences require attribution, so every card credits its source where students actually see it, not just in the README.
- *Why doesn't the frontend build the YouTube link?* One place builds links: the backend. Citations and source cards then always agree on the video and the timestamp offset, and a frontend bug can't send you to the wrong video.
- *Could a malicious attribution inject code?* No. It's rendered as plain text, never as HTML or Markdown.
- *Where do the licence values come from?* The course manifest, `manifest.csv`, which records each file's licence and attribution and is read when the demo notebook is built.

---

## FX1 · App font

**What it does:** Every page now uses Geist, the sans-serif font the app was meant to have, instead of the browser's default serif.

**Data flow:** `app/layout.tsx` loads Geist with next/font, which self-hosts the font files (no request to Google at runtime) and exposes it as the CSS variable `--font-geist-sans` on `<html>` → `app/globals.css` maps Tailwind's `--font-sans` to `var(--font-geist-sans)` → the body uses `font-sans`, so every page inherits Geist. Code uses Geist Mono the same way through `--font-mono`.

**Main files:**
- `frontend/app/globals.css`: the theme line `--font-sans: var(--font-geist-sans);`.
- `frontend/app/layout.tsx`: loads Geist and Geist Mono and puts their variables on `<html>`.

**A judge might ask… / my answer:**
- *Why was the app in a serif font before?* The shadcn theme defined `--font-sans` as `var(--font-sans)`, which refers to itself. CSS treats that as invalid, so the page had no font set and the browser fell back to its default serif. Pointing it at the font next/font actually loads fixed it.
- *Does the font slow the page down?* No. next/font downloads it at build time and serves it from our own site, with a size-matched fallback so the layout doesn't jump while it loads.

## L1 · Free-tier model chain

**What it does:** Every Gemini call goes through a chain of models: gemini-3.8-flash first, then the fallbacks listed in `GEMINI_FALLBACK_MODELS` (gemini-3.5-flash, then gemini-3.1-flash-lite). On the free tier each model has its own small daily quota (gemini-3.8-flash: 5 requests a minute, 20 a day), and Google Cloud billing couldn't be set up, so a paid tier wasn't an option. When a model is out of quota or overloaded, the next one answers, and `/ask` reports which model it was. One deadline caps the whole chain, so a request never hangs.

**Data flow:** `POST /v1/notebooks/{nb}/ask` → `app/chat/answer.py` → `generate_json_with_model` in `app/llm/generate.py`:
- The chain is `[GEMINI_MODEL] + GEMINI_FALLBACK_MODELS` (from `gemini_model_chain` in `app/config.py`; spaces, empty names and duplicates removed).
- **Cache first:** `llm_cache` is looked up under every model's key, in chain order. A hit returns the stored answer and that model's name with no call and no write.
- **Then the models, in order.** With 2 or more models, a model that returned 429 earlier is skipped until its retry time. Each model gets up to 3 attempts on 5xx errors, timeouts and connection errors. Every attempt's HTTP timeout is recomputed as min(60 s, time left of the request's 150 s deadline), and no attempt starts with under 1 s left.
- A 429 marks the model as skipped until its retry time and moves on; a model still failing after its attempts moves on; any other 4xx stops the chain (a logged 500).
- **First success:** validated against the schema, cached under the answering model's key, and returned as (answer, model); `/ask` puts that model in the response.
- **All failed:** if every model was out of quota, 429 `quota_exhausted` with the shortest wait; otherwise 503 `unavailable` (Retry-After: 30).

**Main files:** `backend/app/config.py`, `backend/app/llm/generate.py`, `backend/app/chat/answer.py`, `backend/app/api/ask.py`, `backend/tests/test_llm.py`, `backend/tests/conftest.py`, `.env.example`, `docs/architecture/stack.md`.

**A judge might ask… / my answer:**
- *Why not just pay for more quota?* Creating a Google Cloud billing account failed with error OR_BACR2_59, a failure other users report with no working support path, so paid Gemini, Vertex AI and Cloud Run were all unavailable. The free tier gives each model its own daily quota, and the chain uses them one after another, all within one project and one key.
- *Isn't a fallback model's answer worse?* It may be phrased less well, but grounding doesn't depend on the model: every model sees the same retrieved passages and the same rules, and must cite numbered sources the backend checks. The response says which model answered, and the evaluation measures it. In testing, gemini-3.1-flash-lite answered "What is conditional probability?" with citations to textbook pages 70–72, the right pages.
- *Why skip a model after a 429?* Google says how long to wait. Asking again sooner only adds a failed round trip, so the model is skipped until then. The skip list lives in memory and resets when the server restarts, and it's off when there's only one model, so a one-model setup behaves exactly as before.
- *Why one 150-second deadline?* Without it, 3 models × 3 attempts × 60 s could take 9 minutes, and the browser gives up after 5. Every attempt gets only the time that's left.
- *Does the cache still make repeats free?* Yes. It's checked under every model's key before any call. In testing, the repeat question came back in 196 ms with no Gemini call.
- *How was it tested without spending quota?* A fake client replays 429s, 503s and timeouts. The deadline is tested with an injected clock and by checking the exact stop value and timeouts the code computes, never by waiting. A fixture pins the older tests to one model, so they don't depend on what's in `.env`. Only the final checks made real calls: one question each.
- *What went wrong while building it?* The plan review caught two bugs before any code was written: a 429 would have triggered the skip even with a single model, changing existing behaviour, and the 150 s budget was checked only between attempts, so one request could run to about 280 s. The verify then found that the timeout was still set once per model rather than once per attempt. A fake-clock simulation ran to 183.7 s against the 150 s deadline, so the timeout is now recomputed at the start of every attempt, and the re-verify's simulation of the same scenario stops at exactly 150 s. The verify also found that the existing tests silently picked up the fallback models from `.env`, which a fixture now prevents. During the first live check, the first two models failed and the third answered with correct citations: the chain doing its job for real. In the re-verify a few hours later, the primary model answered directly.

---

## F2a · Three-panel notebook page

**What it does:** On a laptop-sized screen, a notebook opens as a full-window workspace, like NotebookLM: a slim header with the notebook's name and your account, your sources on the left (with "Add a source" below them), the question box and answers in the middle, and, when you open a PDF or click a citation, the document on the right. Each panel scrolls on its own, so you can read the cited page while the answer stays in view. On a phone the panels stack, and clicking a citation scrolls down to the document.

**Data flow:** unchanged from C4a, C4b, C5 and F1b. The page makes the same requests (`getNotebook`, `listSources`, `uploadSource`, `ask`), with the same query keys and timeouts. Only the layout changed: a header bar, then a `<main>` with three panels (`aside` Sources, `section` Ask, `aside` PDF viewer). The right panel exists only while the viewer context has an open source, so opening a citation adds it and closing the viewer removes it. The account bar has a compact one-line mode used only in this header.

**Main files:**
- `frontend/app/notebooks/[id]/page.tsx`: the header and the three panels.
- `frontend/components/account-bar.tsx`: the new `compact` mode; the default mode is unchanged on /notebooks and /settings.
- `frontend/components/viewer-context.tsx` and `frontend/components/pdf-viewer.tsx`: unchanged; they decide when the right panel shows and what it displays.

**A judge might ask… / my answer:**
- *Why three panels?* Studying from sources means reading an answer and checking the page it cites at the same time. Keeping sources, answers and the document side by side removes the scrolling back and forth.
- *Why is there no topic list yet?* The topics come from the backend's topic tagging, which lands next. The left panel already has the spot for it, above the sources.
- *Does it work on a phone?* Yes. Below 1024 pixels the panels stack, and clicking a citation scrolls to the document.
- *Did the redesign risk breaking anything?* It changed markup and styles only. Every request, error message and timeout stayed the same, and a separate check compared the code before and after to confirm it.

- *What went wrong while building it?* Nothing broke, but moving the citation button into its own component left an unused callback behind in the notebook page. Before deleting it, the re-verification read the previous commit's code to prove the callback only opened the PDF. The narrow-screen "scroll down to the viewer" lives in an effect that watches the viewer's navigation counter, so it still fires for every citation, wherever the button is.

---

## FX2 · Demo polish

**What it does:** The app now introduces itself. The login page shows the name, Groundwork, with a one-line tagline ("Study from your own course material, with every answer cited to the page, slide or moment it came from.") and a hint under the guest button that no account is needed to try the demo. Every tab has a proper title ("Notebooks · Groundwork") and a black-and-white icon. A mistyped address shows a "Page not found" card with a way back, and an unexpected crash shows a "Something went wrong" card with "Try again" instead of a blank page. A brand-new account's notebook list says what to do next instead of looking empty.

**Data flow:** no requests changed. `lib/site.ts` holds the name, tagline and guest hint as constants → `app/layout.tsx` sets the default title, the title template `%s · Groundwork` and the description → metadata-only `layout.tsx` files in `login/`, `notebooks/`, `notebooks/[id]/` and `settings/` fill in each page's title (server files that only return their children, because the pages are client components and can't export metadata) → `app/not-found.tsx` handles any unknown address with a real 404 → `app/error.tsx` catches render errors below the root layout, logs them to the console and offers a retry → `/notebooks` shows its empty state when the first page holds no notebook of your own and there's no next page, using data the list already loaded.

**Main files:**
- `frontend/lib/site.ts`: the name, tagline and guest hint, in one place.
- `frontend/app/layout.tsx`: the title template and description.
- `frontend/app/login/layout.tsx`, `notebooks/layout.tsx`, `notebooks/[id]/layout.tsx`, `settings/layout.tsx`: tab titles.
- `frontend/app/login/page.tsx`: the heading, tagline and guest hint.
- `frontend/app/not-found.tsx` and `frontend/app/error.tsx`: the 404 and crash screens.
- `frontend/app/notebooks/page.tsx`: the empty state.
- `frontend/app/icon.svg`: the tab icon (replacing the default favicon).

**A judge might ask… / my answer:**
- *Why does the error page hide the error message?* Error messages can contain internals: file paths, database details, sometimes parts of URLs. The student gets a plain message and a "Try again" button, and the full error goes to the browser console for debugging.
- *Why one file for the product name?* The name appears on the login page, in every tab title and in the description. Keeping it in one constant means a rename is one edit and the app can never disagree with itself.
- *Why tiny layout files just for titles?* The pages are client components, because they use sign-in state and live data, and Next.js only reads page metadata from server files. A metadata-only layout gives each page a real title without moving any data fetching to the server.
- *Why doesn't the tab show the notebook's name?* That would mean setting the title from the browser after the data loads, which can fight Next.js's own title handling. A plain "Notebook" title can't break, and the name is right there on the page.
- *Why tell guests their data is lost on sign-out?* A guest is a real but anonymous account; once you sign out there's no way back into it. Saying so up front is better than a judge losing their quiz progress by surprise.
- *Why a black square for the icon?* A black outline disappears on a dark tab bar; a black square with a white mark is visible on both light and dark ones, and keeps the app monochrome.


---

## V1 · Numerical verifier

**What it does:** Checks that a numerical quiz question's answer is actually right, by running a short piece of Python that computes it with exact fractions. For each numerical question, the question generator (S6) writes a stated answer such as "1/221" and a small snippet such as `answer = comb(4, 2) / comb(52, 2)`. The verifier refuses anything outside a tiny arithmetic language, runs the snippet in a separate locked-down Python process, and passes the question only if the computed fraction equals the stated answer exactly. A second function grades what students type: "3/8", "0.375" and "37.5%" all count for 3/8, and "0.333" counts for 1/3, but "0.33" doesn't.

**Data flow:**
- `verify_numerical(solution_code, stated_answer)` in `app/questions/numerical.py`:
  1. `parse_exact` reads the stated answer: an integer, `a/b`, or an exact decimal, matched with one `re.fullmatch` and built from its digits as a `Fraction`. Anything else returns `bad stated answer:`.
  2. `check_snippet` parses the snippet with `ast` and checks every node against a whitelist. Allowed: assignments, `for`, `if`, arithmetic, comparisons, comprehensions, small lists, sets and dicts, and calls to ten named functions (`Fraction`, `comb`, `perm`, `factorial`, `sum`, `min`, `max`, `abs`, `len`, `range`). Not allowed: imports, attribute access, strings, floats, `def`, `lambda`, `while`, or any name starting with `_`. A failure returns `rejected:` with the reason, and no process is started.
  3. A child process starts: the base Python interpreter with `-I -S -B` (isolated: no environment variables, no site-packages, no .pyc files) running `app/questions/numerical_sandbox.py`. The snippet is sent as UTF-8 bytes on stdin, with a 2-second timeout.
  4. Before reading anything, the child caps its own memory at 512 MiB: a Windows Job Object through `ctypes`, or `RLIMIT_AS` on Linux. If the cap can't be applied, it refuses to run the snippet.
  5. The child checks the snippet again, then rewrites `+ * / **` into guard functions and wraps every loop in a shared counter. It runs the result with only four builtins. `/` becomes `Fraction(a, b)`, so division is always exact. The guards stop numbers above 2,000,000 bits, exponents above 10,000, `comb`/`perm`/`factorial` above 10,000, ranges over 1,000,000 items, and more than 1,000,000 loop items in total.
  6. The child prints one JSON line, such as `{"ok": true, "num": "1", "den": "221"}` or an error kind with a message. The parent checks its shape, then compares the fractions.
  7. The result is `NumericalVerification {method: "python", passed, detail, computed}`. `detail` always starts with `ok:`, `mismatch:`, `bad stated answer:`, `rejected:`, `limit:`, `error:`, `timeout:` or `crash:`, so the generator can be told exactly what went wrong.
- `grade_numerical(student_answer, correct_answer)`:
  1. The correct answer goes through `parse_exact`.
  2. The student's text goes through one `re.fullmatch`, which accepts a fraction, a decimal, a comma-grouped integer, and an optional `%`.
  3. An exact match is correct. Otherwise, a decimal with at least 3 significant figures that equals the correct value rounded or truncated to the same number of places is correct.
  4. It never raises on student input.
- Nothing here touches Firestore, Gemini or the network.

**Main files:**
- `backend/app/questions/numerical.py`: `parse_exact`, `grade_numerical`, `verify_numerical`, `NumericalVerification`, and `SNIPPET_RULES` (the rules text the S6 generation prompt includes word for word).
- `backend/app/questions/numerical_sandbox.py`: standard library only. It holds the whitelist, the syntax-tree rewrite, the guards, the memory cap and the child's entry point.
- `backend/tests/test_numerical.py`: 191 tests.

**A judge might ask… / my answer:**
- *Why not trust the AI's answer?* Language models make arithmetic slips, and a quiz that marks a correct student wrong is worse than no quiz. So the model also has to write code that computes the answer, and a question enters the bank only if the code and the stated answer agree exactly. A question that fails is rejected, never shown.
- *Why fractions instead of decimals?* This course's numerical answers are exact fractions. With floats, 0.1 + 0.2 isn't 0.3, so an exact comparison fails correct answers, and a tolerance lets near-misses through. Fractions make "equal" mean equal: `comb(4, 2) / comb(52, 2)` is exactly 1/221.
- *Isn't running AI-written code dangerous?* Yes, which is why there are five layers. The whitelist rejects anything that isn't arithmetic before any process starts. The child gets only four builtins. Guards cap the size of numbers, exponents and loops. The child caps its own memory at 512 MiB. And it is killed after 2 seconds. The suite has 40 hostile snippets (imports, `__import__`, attribute tricks, `eval`, huge loops, `10 ** 10 ** 10`), and a second AI model invented 35 more. Every one ended as rejected, limit, error or timeout; none crashed, and no child got past 512 MiB.
- *If the whitelist blocks everything, why a separate process?* Defence in depth. Also, Python can't reliably stop a runaway thread, but it can always kill a process, and the memory cap applies per process.
- *Why at least 3 significant figures for decimals?* It's the usual exam convention. A 2-figure rule would accept anything from 0.325 to 0.335 for 1/3, wide enough to pass answers from a slightly wrong method. Exact forms like 1/3 are always accepted.
- *What if a student types nonsense?* It's graded wrong, never an error. A fixed-seed fuzz test sends 2,000 random strings of digits and symbols, and verification sent 20,000 more.
- *Where does it run?* The question bank is built by a script on the laptop (Windows), so `verify_numerical` runs there. `grade_numerical` runs in the hosted backend for every quiz answer. Both platforms are handled.
- *Known limits?* Subtraction, modulo and floor division aren't size-guarded; the memory cap and the timeout bound them, which the verification confirmed. Extremely deep expressions (about 330 chained terms) fail safely with a recursion error. The Linux memory-cap branch is exercised only on Linux, at deployment.
- *What went wrong while building it?*
  - The plan review measured a snippet that passed every check allocating memory at 1.8 GiB/s on Windows, which has no built-in memory limit for a child process; the Job Object cap fixed that. The review also found that formatting an error message could itself raise (a `KeyError` with a 20,000-bit key) and that non-UTF-8 error output broke the crash path.
  - The final verification found three more. `grade_numerical` crashed on "1.2.3", which is now parsed with one full-string match and fuzz-tested. Windows text mode turned every `\n` into `\r\n` on the way to the child, so a snippet the parent accepted at 2,000 characters was too long for the child; the snippet is now sent as bytes. And the exponent cap was 20,000 instead of 10,000.
- *How was it tested?* 191 tests in about 25 seconds, with no Gemini and no Firestore. Each hostile snippet breaks exactly one rule, and its test checks that this specific rule fired, not just that something did.

---

## I3a · Lecture transcription

**What it does:** A laptop-only script turns the six demo lecture videos into timestamped transcripts, so the app can later cite a lecture down to the moment an idea is explained. It runs Whisper (faster-whisper with the `medium.en` model) on the laptop's CPU. It never uses the Gemini API, never touches Firestore or Storage, and writes only outside the repo. The first full run transcribed 5 h 2 min of audio in 3 h 31 min overnight (0.70 seconds of work per second of audio), with no failures.

**Data flow:** `uv run --env-file ../.env --group laptop python scripts/transcribe_lectures.py` → finds `videos\L01.mp4` to `L06.mp4` in `COURSE_DATA_DIR` → reads each video's length from its container (PyAV, no decoding) → skips lectures that already have a valid finished transcript → loads the Whisper model once → for each remaining lecture, transcribes with voice-activity detection on, English, beam size 5, not conditioned on the previous text, plus a short prompt of course terms (Bayes' rule, PMF, Tsitsiklis…) → writes `derived\transcripts\L01.txt` (one "[mm:ss] text" line per segment, for reading) and then `L01.json` (model, settings, duration, timing, and every segment with start, end, text and three confidence measures). Times are seconds from the start of the video file. Each file is written to a `.tmp` and then renamed into place, so a crash never leaves a half-written transcript that looks finished. Progress goes to the console and `transcribe.log` every 5 minutes of audio. A `--clip-seconds` trial transcribes just the start of one lecture into `_trials\` and estimates how long the full run will take. Task I3b turns these segments into searchable chunks with YouTube timestamps.

**Main files:** `backend/scripts/transcribe_lectures.py`, `backend/tests/test_transcribe_lectures.py`, `backend/pyproject.toml` (faster-whisper and an `av<19` cap in the `laptop` dependency group), `backend/uv.lock`.

**A judge might ask… / my answer:**
- *Why transcribe locally instead of using an API?* It's free, it never touches the small daily Gemini quota, and it's reproducible from the video files alone. Whisper runs fine on a laptop CPU: about 35 minutes per 50-minute lecture.
- *Why medium.en and not a smaller model?* A 5-minute trial estimated the full run at about 6 hours, inside the overnight window. Accuracy on terms like "PMF" and "Bayes" matters because retrieval searches this text. The real run was faster than the trial (the trial ran while other programs were using the CPU).
- *How precise are the timestamps?* Whisper gives a start and end for every segment, usually a few seconds long, measured from the start of the video file. That's precise enough for a citation to open the YouTube video within seconds of the example.
- *Why voice-activity detection and "not conditioned on previous text"?* Long recordings can send Whisper into a loop that repeats the same sentence. Skipping silences and not feeding each window the previous text are the standard defences; each segment also stores confidence measures, so suspicious ones can be filtered later. A check of all six transcripts found no repetition loops and coverage to within 1 second of each video's end.
- *What if the laptop dies mid-run?* Re-running the same command skips every finished lecture and redoes only the interrupted one. Transcripts are written to a temporary file and renamed only when complete, so a half-finished file is never mistaken for a finished one.
- *Is it in the hosted app?* No. Whisper lives in a separate `laptop` dependency group that the server image never installs. Videos are ingested only on the laptop.
- *What went wrong while building it?* The plan review caught that the timer must cover the whole transcription (faster-whisper does its work lazily, while the results are read, not when the function is called), and that `--force` had to delete the old transcript first so a crash couldn't leave an old "finished" file next to new text. The verify then ran the script for real on a 30-second clip, even though all 101 tests already passed, and it crashed: PyAV 19 had removed an argument that faster-whisper still passes, so every lecture would have failed overnight. Capping PyAV below 19 fixed it. The verify also found that a trial run combined with `--force` would delete a real finished transcript; trial runs now never touch the real files, and a test compares them byte for byte. Overnight, the laptop's lid setting turned out not to be adjustable, so the lid simply stayed open while sleep on AC was switched off.

---

## I1 · Page labels, licence pages, slides and one citation builder

**What it does:** Every PDF is now read the way a student sees it. Citations show the page number printed in the book ("Grinstead & Snell … p. 137"), not the PDF's internal page number, while the viewer still opens the right physical page. Licence and terms pages (the GNU FDL notice, MIT OpenCourseWare's terms page at the end of every slide deck and recitation) are recognised and never searched or cited. Slide handouts with four slides per page are split into separate slides, each with the exact rectangle it occupies on the page, so a citation can highlight the one slide it comes from. One function builds every citation, so the ask box, chat and the topic Sources button always show identical labels and links.

**Data flow:** upload or the demo-course script → the parse function in `app/ingestion/pdf.py` reads each page's text once with PyMuPDF (`get_text("dict")`) →
- **Licence check:** a short page (under 400 characters) mentioning `ocw.mit.edu/terms`, or a page with both "GNU Free Documentation License" and "freely redistributable", is recorded in `licence_pages` and produces no chunks.
- **Page labels** (`labels.py`): the PDF's own labels if it has any; otherwise the printed number at the start or end of each page's top or bottom line. A number counts only when at least two pages agree on the same gap between printed and physical page. Pages between agreeing pages inherit that gap, and a chapter-opening page with no number takes the gap of the range that follows.
- **Slides** (`slides.py`, only when the source has a `slide_grid` such as "2x2"): finds each slide's frame among the page's drawn rectangles (ignoring small shapes and resolving nested borders), falls back to equal cells if it can't find exactly four, assigns every text line to the slide containing its centre, and converts each frame to the page as displayed (the rotation and crop handled by PyMuPDF's rotation matrix).
- **Chunks:** at most 400 tokens, never crossing a page or a slide, each with a location: page, printed label, and slide number and rectangle for slides.
- **Stored:** the source records `page_labels`, `licence_pages`, `slide_grid` and `viewer_path`.
- **Citing:** `/ask` maps the model's source numbers to chunks → `build_citation` in `app/chat/citations.py` → label "{title} p. {printed page}" (+ " (slide n)") and `open` {pdf, physical page, rectangle for slides only}.

**Main files:** `backend/app/ingestion/pdf.py`, `labels.py`, `slides.py`, `models.py`; `backend/app/chat/citations.py`, `answer.py`; `backend/app/db/sources.py`; `backend/app/api/sources.py`; `backend/tests/test_pdf_ingestion.py`, `test_citations.py`.

**A judge might ask… / my answer:**
- *Why printed page numbers?* Students look things up in the book by the number printed on the page. The demo textbook is an extract, so PDF page 70 is printed page 137: citing "p. 70" would send them to the wrong place. The viewer still opens the physical page, so the link lands exactly.
- *Why not just type in the page numbers?* Detection works for any uploaded PDF, not just ours. The course's own page table is used only as a test: the detector reproduces all 165 labels of the demo textbook with no mistakes.
- *Why exclude licence pages?* They're legal boilerplate. Without exclusion, "MIT OpenCourseWare terms" text gets retrieved for real questions and can even be cited as course content.
- *How do you know which slide a sentence belongs to?* Each slide's frame is a drawn rectangle on the handout page. Every text line goes to the frame that contains its centre point. Whole text blocks couldn't be used, because PyMuPDF often merges the left and right slides' lines into one block.
- *Does the highlight land on the right spot if the PDF is rotated or cropped?* Yes. PyMuPDF reports positions on the unrotated page, so every rectangle is converted with the page's rotation matrix. The verify checked a rotated page with an offset crop: the stored rectangle matched the rendered frame within 1 pixel, and the test asserts the hand-computed rectangle.
- *Do my own uploads get slide highlights?* Uploads get printed page labels and licence-page exclusion. Slide splitting needs to know the layout (four slides per page), which the demo course records in its manifest, so only the demo's slide handouts get highlights in v1.
- *Did this slow uploads down?* Parsing the 165-page textbook went from 2.5 s to 3.6 s. An upload takes about 70–110 s, almost all of it computing embeddings.
- *What went wrong while building it?* The plan review caught that the plan assigned text to slides by PyMuPDF's text *blocks*, which span both columns, so the second slide's title "Review of probability models" would have been cited as part of slide 1. Lines never cross frames, so assigning per line fixed it, with a real-file test. The review also found that one phrase in the original licence rule never appears in the real GFDL notice (it was replaced with one that does). The verify confirmed every label, 52 slides with no lost characters, and the box positions by rendering the pages and drawing the boxes. I1 was then committed together with I2 (see I2), because I2's draft code had been written into a file I1 also changed.

---

## I2 · Topics and the demo notebook

**What it does:** One laptop script builds the shared demo notebook from the course manifest: 20 sources (6 slide decks, 12 recitations, the textbook and the syllabus), 399 searchable chunks, and the six syllabus topics with their prerequisites. Every chunk is tagged to a topic without using Gemini. Two new endpoints serve the topic list and each topic's Sources button: a list of citations, one per page (or per slide), built by the same function as every other citation. The lecture videos join the same notebook in I3b.

**Data flow:**
- **Build:** `uv run --env-file ../.env python scripts/ingest_course.py` (from `backend/`) → refuses to run (exit 2) unless it's pointed at the local emulators → reads `manifest.csv`, which now has `slide_grid`, `topics` and `source_url` columns → parses `syllabus.md` into topics t1–t6 (IDs from the topic number) with summaries and prerequisites → sends each manifest row to a handler by kind:
  - slide decks and PDFs: store the original, parse with I1's function (passing `slide_grid`), tag each chunk from the row's `topics` value, embed, write the chunks, and only then mark the source ready;
  - the syllabus: stored, with no chunks;
  - videos: skipped until I3b.
  
  Source IDs come from the file name (`src_l02_slides`), and `ref_n` is the manifest row, so a re-run lands on the same documents. At the end of every run the script **rebuilds** the notebook's source summary, chunk count, status and all topic documents from what's stored, reading only each chunk's source, location and topic (never its embedding).
- **Tagging:** a lecture's files go to its topic. The textbook uses physical page ranges taken from its section headings ("Independent Events" starts on page 75, Bayes' Formula on page 83, and so on). Where a range genuinely mixes two topics ("t5|t6"), each chunk goes to the topic whose description it is most similar to, by embedding.
- **Topic locations:** each topic stores one entry per distinct page (or slide), keeping one chunk ID per entry, so "Sources (35)" always means 35 chips.
- **API:** `GET /v1/notebooks/{nb}/topics` → readable-notebook check → the topic documents in order. `GET /v1/notebooks/{nb}/topics/{t}/sources` → the topic ID must fully match `t1`–`t6` or `other` before anything is read → one topic document → source titles from the notebook's own summary (no extra reads) → `build_citation` for each location.

**Main files:** `backend/scripts/ingest_course.py`, `backend/app/ingestion/syllabus.py`, `topics.py`; `backend/app/db/topics.py`, `sources.py`, `notebooks.py`, `paths.py`; `backend/app/api/topics.py`; `backend/app/models/topic.py`; `backend/tests/test_topics.py`, `test_ingest_course.py`.

**A judge might ask… / my answer:**
- *Why tag topics without the AI?* It's free, instant and repeatable. The course already defines what belongs where: each lecture's slides and recitations are one topic, and the textbook's sections map to topics by their headings. Embedding similarity settles only the pages that genuinely mix two topics.
- *How do you know the topic map is right?* It was built from the real section headings on the pages and checked by a second model, which found an error (the independence section starts on page 75, not 77) before anything was built. The verify then reported how the ambiguous pages were split, page by page, and the split matched the content.
- *Can the build script be run twice?* Yes. Source IDs and chunk IDs are deterministic, a source counts as ready only after all its chunks are written, and the totals and topic lists are recomputed from scratch at the end of every run instead of being added to. A second run takes about 5 seconds and changes nothing.
- *Why does a topic list "Sources (35)" and not one entry per chunk?* Several chunks can come from the same page. Grouping by page (or slide) keeps the count equal to what a student can open.
- *Why don't my own notebooks have topics?* The six topics belong to the demo course. Inventing an "Other" topic for uploads would cost a read of every chunk; asking a question across the whole notebook already works.
- *Can the script damage real data?* It refuses to run unless the emulator settings point at a local `demo-` project. Publishing to the real project is a separate step in deployment.
- *What went wrong while building it?*
  - The planning agent started writing code before its plan was approved, into a file that I1 had also changed, so I1 and I2 were verified and committed together.
  - Before that was caught, a formatter had been run across the whole backend and silently rewrapped 15 committed files from other tasks; a line-ending-insensitive diff found them, and they were restored from git. Agents now never run formatters.
  - The plan review found the chapter-4 topic map off by two pages, a chunk count that would have doubled on every re-run, and tests that would have overwritten the shared demo notebook. All were fixed in the plan.
  - The verify found that the script only worked when run as a module, and that one older test assumed no demo notebook exists. Both were fixed and re-verified.