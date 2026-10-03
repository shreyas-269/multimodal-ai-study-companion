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