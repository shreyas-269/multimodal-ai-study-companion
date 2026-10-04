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