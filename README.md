# Groundwork · Multimodal AI Study Companion

> A source-grounded multimodal AI study companion for **Track D (Personalized Tutoring & Adaptive Learning)** at the **IIT Mandi Multimodal AI Hackathon 2026**.

[![Live App](https://img.shields.io/badge/Demo-Live%20on%20Vercel-black?style=flat&logo=vercel)](https://multimodal-ai-study-companion.vercel.app)
[![Demo Video](https://img.shields.io/badge/YouTube-Watch%20Demo-red?style=flat&logo=youtube)](https://youtu.be/tmQuO6GslfU)
[![Track](https://img.shields.io/badge/Track-D%3A%20Personalized%20Tutoring-blue?style=flat)](#)

---

## Quick Links

- **Live Hosted Application:** [https://multimodal-ai-study-companion.vercel.app](https://multimodal-ai-study-companion.vercel.app)
- **Demo Video (YouTube):** [https://youtu.be/tmQuO6GslfU](https://youtu.be/tmQuO6GslfU)
- **Hosted Backend (Render Free Tier):** [https://multimodal-ai-study-companion.onrender.com/v1/health](https://multimodal-ai-study-companion.onrender.com/v1/health)

---

## Core Capabilities & Architectural Highlights

1. **Multimodal Source Grounding:**
   - Ingests heterogeneous course assets: 165 textbook pages, 6 lecture slide decks, 12 recitations, and 6 full lecture video recordings (5h 02m transcribed locally via Faster-Whisper `medium.en`).
   - Resolves multi-slide handout grids into displayed (rotated) PDF coordinates, rendering exact quadrant bounding boxes in an HTTP Range-streamed PDF viewer (`206 Partial Content`).
   - Uses local `BAAI/bge-small-en-v1.5` embeddings to refine lecture transcript timestamps to a two-segment audio window, landing on YouTube within 9.9s of concept introductions (e.g., L02 radar detection at `t=1505s`).

2. **Zero Arithmetic Hallucination via Python Sandboxing:**
   - Numerical problems are never graded by language model estimates.
   - Calculations execute in an AST-sandboxed Python subprocess using exact rational arithmetic (`fractions.Fraction`) with OS memory limits (Windows Job Objects / Linux `RLIMIT_AS` capped at 512 MiB) and a 2-second execution timeout.
   - Multiple-choice questions undergo blind second-model verification before entering the question bank. Answer keys, explanations, and citations never leave the backend before student submission.

3. **Principled Bayesian Learner Model (Study Coach):**
   - Sealed Bayesian Knowledge Tracing (BKT) engine tracking mastery per syllabus topic with standard parameters (prior $0.30$, slip $0.10$, learn $0.15$, guessing floor by question type).
   - Dynamic 14-day exponential forgetting decay ($p = 0.3 + (p - 0.3) \times 0.5^{\text{days}/14}$) applied lazily at evaluation time without rewriting database history.
   - Prerequisite-aware "Study Next" algorithm topologically prioritizing foundational axioms before advanced topics.
   - Non-optimistic, server-authoritative checkbox synchronization (`PUT /v1/notebooks/{nb}/topics/{t}/checked`).

4. **Strict Boundary Enforcement:**
   - Questions outside course scope (e.g., *"What is the capital of France?"*) are refused or isolated strictly inside a dedicated "Beyond your course" container, never citing course materials.

---

## Quantitative Benchmark Results (Task S9 / Checkpoint 3)

Evaluated across 25 curated test cases (22 in-course theoretical and application queries across topics 1–6, plus 3 out-of-domain edge cases):

| Metric | Result | Benchmark Target | Pass / Fail |
| :--- | :---: | :---: | :---: |
| **Context Key-Term Recall** | **92.3%** (20/22 cases $\ge 75\%$) | $\ge 80.0\%$ | **PASS** |
| **Citation Recall (Overall)** | **81.5%** | $\ge 75.0\%$ | **PASS** |
| ↳ *Lecture Slides Citation Recall* | **100.0%** | $\ge 80.0\%$ | **PASS** |
| ↳ *Textbook Citation Recall* | **87.5%** | $\ge 75.0\%$ | **PASS** |
| ↳ *Lecture Video Citation Recall* | **83.3%** | $\ge 70.0\%$ | **PASS** |
| **Out-of-Domain Refusal Compliance** | **100.0%** (3/3 cases) | $100.0\%$ | **PASS** |
| **Evaluation Latency (Cached)** | **p50 = 0.2s, p90 = 0.9s** | $< 2.0\text{s}$ | **PASS** |
| **Live Gemini API Quota Spent** | **0 tokens** (Deterministic `llm_cache`) | Free Tier Budget | **PASS** |

---

## One-Command Local Reproduction (Judges' Docker Setup)

Judges can run the entire authenticated stack locally without installing Python, Node, or cloud dependencies.

### Prerequisites
- Docker Desktop (with Compose V2 enabled)
- A free-tier Google Gemini API key

### Instructions
1. Clone the repository and navigate to the project root:
   ```bash
   git clone https://github.com/shreyas1407/multimodal-ai-study-companion.git
   cd multimodal-ai-study-companion
   ```
2. Create `.env` in the repository root:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```
3. Start the multi-container stack:
   ```bash
   docker compose up
   ```
4. Access the services:
   - **Frontend Workspace:** [http://localhost:3000](http://localhost:3000) (Click *"Continue as guest"*)
   - **Backend API & Swagger Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
   - **Firebase Emulator UI:** [http://localhost:4000](http://localhost:4000)

*Note: The local Docker image includes the pre-ingested MIT OCW 6.041 course, pre-computed vector embeddings, and pre-warmed `llm_cache`.*

---

## Course Materials & Open License Attribution

- **MIT OpenCourseWare 6.041 / 6.431: Probabilistic Systems Analysis and Applied Probability** (Fall 2010)  
  *Instructor:* Prof. John Tsitsiklis  
  *License:* [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)](https://creativecommons.org/licenses/by-nc-sa/4.0/)  
  *Materials Used:* Lectures 1–6 (Videos, Slides, Recitations, and Solutions).
- **Introduction to Probability**  
  *Authors:* Charles M. Grinstead and J. Laurie Snell (American Mathematical Society)  
  *License:* [GNU Free Documentation License (GFDL)](https://www.gnu.org/licenses/fdl-1.3.html)  
  *Materials Used:* Sections 1.2, 3.1–3.2, 4.1, 5.1, 6.1–6.2.

All source documents and citations display their respective license and attribution badges directly within the application UI.

---

## AI Assistant Transparency Disclosure

In adherence to hackathon guidelines, development utilized AI coding and review assistance:
- **Architecture, Planning & Implementation:** Gemini 3.8 Flash (via Google Antigravity).
- **Independent Plan Reviews & Verification Audits:** Claude Code (Claude 3.7 Sonnet).
- All engineering decisions, acceptance criteria, and final Git commits were governed and executed by Shreyas.
