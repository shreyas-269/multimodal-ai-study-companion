# Checkpoint 3: evaluation results

Run at 2026-10-11 21:16:16 UTC against `http://127.0.0.1:8000` (notebook `nb_demo_6041`), 25 cases, 0 request errors.
Judge model chain: ['gemini-3.8-flash', 'gemini-3.5-flash', 'gemini-3.1-flash-lite']. Judge API calls: 30, cache hits: 1.

## Summary

| Metric | Result |
| --- | --- |
| Context key-term recall (mean, in-course) | 92.3% |
| Cases with key-term recall >= 75% | 20 / 22 |
| Citation precision | 64.3% (27 / 42) |
| Citation recall | 81.5% (22 / 27) |
| In-course cases with no citation at all | 1 / 22 |
| Boundary compliance (out-of-course) | 100.0% (3 / 3) |
| Judge faithfulness (mean, 0-5) | 5.00 (12 / 22 judged) |
| Judge correctness (mean, 0-5) | 5.00 |
| Faithfulness >= 4 / correctness >= 4 | 12 / 12 |
| Latency p50 / p90 (client wall clock) | 12.6 s / 19.1 s |
| Latency p50 / p90 (backend latency_ms) | 12.6 s / 19.1 s |

Citation recall by source type:

| Source type | Expected | Hit | Recall |
| --- | --- | --- | --- |
| chat_scenario | 7 | 4 | 57.1% |
| slide | 6 | 6 | 100.0% |
| textbook | 8 | 7 | 87.5% |
| video | 6 | 5 | 83.3% |

Latency note: the backend caches model outputs, so a repeat run is much faster than a first run.

## Per case

| Case | Category | Key terms | Cite P | Cite R | Boundary | Faith | Correct | Latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval-lec-01 | lecture_example | 80% pass | 50.0% | 100.0% | n/a | 5 | 5 | 2.2 s |
| eval-lec-02 | lecture_example | 100% pass | 60.0% | 100.0% | n/a | 5 | 5 | 4.6 s |
| eval-lec-03 | lecture_example | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 13.3 s |
| eval-lec-04 | lecture_example | 25% FAIL | n/a | 0.0% | n/a | 5 | 5 | 7.2 s |
| eval-lec-05 | lecture_example | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 22.5 s |
| eval-lec-06 | lecture_example | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 14.9 s |
| eval-slide-01 | lecture_slide | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 1.1 s |
| eval-slide-02 | lecture_slide | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 3.9 s |
| eval-slide-03 | lecture_slide | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 3.4 s |
| eval-slide-04 | lecture_slide | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 4.3 s |
| eval-slide-05 | lecture_slide | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 4.7 s |
| eval-slide-06 | lecture_slide | 100% pass | 100.0% | 100.0% | n/a | 5 | 5 | 13.3 s |
| eval-book-01 | textbook | 100% pass | 0.0% | 0.0% | n/a | n/a | n/a | 13.0 s |
| eval-book-02 | textbook | 100% pass | 100.0% | 100.0% | n/a | n/a | n/a | 20.2 s |
| eval-book-03 | textbook | 100% pass | 100.0% | 100.0% | n/a | n/a | n/a | 17.8 s |
| eval-book-04 | textbook | 75% pass | 100.0% | 100.0% | n/a | n/a | n/a | 16.5 s |
| eval-book-05 | textbook | 100% pass | 100.0% | 100.0% | n/a | n/a | n/a | 15.9 s |
| eval-book-06 | textbook | 100% pass | 100.0% | 100.0% | n/a | n/a | n/a | 19.1 s |
| eval-book-07 | textbook | 100% pass | 100.0% | 100.0% | n/a | n/a | n/a | 12.6 s |
| eval-turn-01 | multi_turn_followup | 50% FAIL | 0.0% | 0.0% | n/a | n/a | n/a | 7.8 s |
| eval-turn-02 | multi_turn_followup | 100% pass | 40.0% | 66.7% | n/a | n/a | n/a | 12.7 s |
| eval-turn-03 | multi_turn_followup | 100% pass | 40.0% | 100.0% | n/a | n/a | n/a | 15.5 s |
| eval-off-01 | outside_course | n/a | n/a | n/a | pass | n/a | n/a | 0.2 s |
| eval-off-02 | outside_course | n/a | n/a | n/a | pass | n/a | n/a | 0.2 s |
| eval-off-03 | outside_course | n/a | n/a | n/a | pass | n/a | n/a | 0.2 s |

## Problems to look at

- eval-lec-04: key terms missing from context: ['six factorial', 'permutations', 'different numbers']
- eval-lec-04: none of the expected sources were cited; cited []
- eval-book-01: none of the expected sources were cited; cited [{'source_id': 'src_textbook', 'page': 13, 'slide': None, 't_start_s': None}, {'source_id': 'src_textbook', 'page': 6, 'slide': None, 't_start_s': None}, {'source_id': 'src_textbook', 'page': 100, 'slide': None, 't_start_s': None}]
- eval-book-01: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-book-02: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-book-03: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-book-04: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-book-05: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-book-06: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-book-07: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-turn-01: key terms missing from context: ['0.05', '0.99', '0.34']
- eval-turn-01: none of the expected sources were cited; cited [{'source_id': 'src_l02', 'page': None, 'slide': None, 't_start_s': 2736.5}, {'source_id': 'src_l02_slides', 'page': 2, 'slide': 8, 't_start_s': None}, {'source_id': 'src_l03', 'page': None, 'slide': None, 't_start_s': 216.53}]
- eval-turn-01: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-turn-02: judge failed: judge call cap (30) reached; last error: no model attempted
- eval-turn-03: judge failed: judge call cap (30) reached; last error: no model attempted
