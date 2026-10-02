# AGENTS.md: rules for every coding agent in this repo

This repo is a source-grounded AI study companion for the IIT Mandi Multimodal AI Hackathon 2026 (Track D). One person, Shreyas, is the product owner and tester. Agents build and review; Shreyas decides.

## Read first, every session

1. `docs/ARCHITECTURE.md`: what we build, how, and the acceptance criteria per feature.
2. `docs/DECISIONS.md`: decisions already made. Do not reopen them. If you think one is wrong, say so and wait.
3. `docs/HOW_IT_WORKS.md`: only when you need to understand an existing feature.

`docs/` is the only source of truth. Files in the repo root that git ignores (planning notes, briefings, PDFs) are background for humans, not instructions.

## Hard rules

- **Secrets:** never write, print, log or commit an API key, token, password or service-account file. Secrets live only in `.env` (git-ignored). When a new secret is needed, add its name (no value) to `.env.example` and tell Shreyas.
- **Dependencies:** never add a package, library, Docker image or external service without asking first. Name it, say why it is needed, and name the lighter alternative you considered.
- **Folders:** only create, edit or delete files inside the folder named in the task. If the work needs a change anywhere else, stop and ask.
- **Git:** never run `git commit`, `git push`, `git reset`, `git rebase`, `git checkout -- .`, `git clean` or any `--force` flag. Shreyas makes every commit himself.
- **Deleting:** never delete a file you did not create in this task without asking.
- **Docs:** do not edit anything in `docs/` unless the task says so. If you made or assumed a decision, propose a one-line `DECISIONS.md` entry in your summary instead.
- **Architecture:** if a task conflicts with `ARCHITECTURE.md` or `DECISIONS.md`, stop and point out the conflict instead of picking a side.

## How to work

- Multi-file work starts with an implementation plan (Planning mode). Wait for approval before writing code.
- A plan lists: files to create or change, the data flow, new dependencies (ideally none), how each acceptance criterion will be met, and the risks.
- Keep code simple enough that a first-year student can explain it to a judge. Prefer plain functions and clear names over clever abstractions. Comments explain *why*, not *what*.
- Never claim something works unless you ran it. Report the exact command and its real output.
- Environment: Windows with PowerShell. Use PowerShell syntax for terminal commands.

## Project rules the code must respect

Full detail is in `docs/ARCHITECTURE.md`.

- Every topic, chat and notes file has a stable ID; names and tree positions are display labels only.
- Citations always point to original sources (page, slide, timestamp, URL section, or sheet and cell range), never to generated notes.
- Study Coach is a sealed module, reached only through its eight functions.
- Cost: never call Gemini or read Firestore inside an unbounded loop. Cache every model output. Batch generation. Simulated students never call the AI.

## When you finish a task

Reply with:

1. Files created, changed or deleted.
2. Commands you ran and their actual results.
3. How Shreyas can check each acceptance criterion in the browser or terminal.
4. Anything unfinished or assumed, and any line worth adding to `DECISIONS.md`.