# Decisions

One line per decision: date, decision, reason. Newest at the bottom. Agents propose entries; Shreyas adds them.

- 2026-10-01: Track D (Personalized Tutoring & Adaptive Learning), solo. Reason: best fit for a source-grounded study companion.
- 2026-10-01: Firebase (Auth, Firestore, Storage) instead of Supabase. Reason: Supabase is blocked in India, so judges might not reach it.
- 2026-10-01: Gemini Flash is the app's AI model. Reason: free tier + AI Pro Cloud credits; Claude and GPT have no free API path; local LLMs are too weak for grounded citations.
- 2026-10-01: Transcription (Whisper) and embeddings run locally. Reason: they never use API quota.
- 2026-10-01: One Python FastAPI backend on Cloud Run holds all logic; the Next.js frontend (Vercel) and the evaluation harness are both its clients. Reason: one place for logic; evaluation scores the same code the demo uses.
- 2026-10-01: Videos are cited as YouTube links with timestamps; no video hosting. Reason: zero cost, exact-second citations.
- 2026-10-01: Hindi/mixed-language support and audio tutoring are out of scope. Reason: time.
- 2026-10-01: Antigravity (Gemini) builds; Claude Code reviews plans, debugs and makes technical calls; Claude models are not used inside Antigravity. Reason: two model families catch each other's mistakes; Claude inside Antigravity was unreliable.
- 2026-10-01: Agents never commit or push; Shreyas commits after every passing feature. Reason: every commit is a known-good save point.
- 2026-10-01: Planning documents stay out of the public repo (.gitignore); docs/ is the agents' only source of truth. Reason: one source of truth, no private notes in public.
- 2026-10-01: Repo layout backend/ frontend/ eval/ docs/ (provisional until 2 Oct). Reason: lets the "only change your assigned folder" rule work.
- 2026-10-02: Antigravity terminal commands set to "Request Review" with an empty Allow list. Reason: a known bug moves Allow-list entries after restart, and loose regex patterns would auto-approve git commit/push.
- 2026-10-02: Antigravity Artifact Review set to "Request Review". Reason: implementation plans must wait for approval (step 4 of the feature loop).
- 2026-10-02: Antigravity model is Gemini 3.8 Flash: Medium by default, Low for small edits, High only for plans touching many files. No Gemini 3.1 Pro or Claude models inside Antigravity. Reason: all Gemini models share one quota pool charged at API prices; Claude stays in Claude Code so two model families check each other.
- 2026-10-02: Claude Code is logged in with the Claude Pro subscription only (no API billing). Sonnet for routine reviews, Opus for hard decisions and bugs. Reason: zero cost; Pro limits are shared with the planning chats.
- 2026-10-02: Planning happens in slice chats (S0 to S13, plus Replan) in the Claude Project, each ending with a handoff; docs/ is re-uploaded to the Project after each slice. Reason: context limits and shared Claude Pro usage.
- 2026-10-02: Demo course is MIT OCW 6.041 (Fall 2010) lectures 1–6 plus Grinstead & Snell sections 1.2, 3.1–3.2, 4.1, 5.1, 6.1–6.2. Reason: YouTube lectures, per-lecture slides, recitations with solutions, exact-fraction numericals Python can verify, a clear prerequisite chain, open licences.
- 2026-10-02: Course files live outside the repo (~/course-data/6041), described by manifest.csv. Reason: size, licence terms (GFDL, third-party problems in OCW files); a fetch script will rebuild them.
- 2026-10-02: Videos come from OCW's archive.org links, not from YouTube; citations use the YouTube IDs with offset 0. Reason: OCW-sanctioned download; durations verified to match within 1 s.
- 2026-10-02: The PowerPoint → PDF path is tested with a student-made .pptx. Reason: no open course ships PowerPoint, and a student's own slides are a realistic upload.