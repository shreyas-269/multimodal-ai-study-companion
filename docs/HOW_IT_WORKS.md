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