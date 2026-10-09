from datetime import UTC, datetime

import pytest

from app.models.citation import Citation, Location, OpenPdfTarget
from app.models.question import (
    QuestionAnswer,
    QuestionOption,
    QuestionVerification,
    StoredQuestion,
)
from app.questions.quiz import (
    QuizInputError,
    build_feedback,
    grade,
    select_questions,
)


def make_dummy_question(
    qid: str,
    qtype: str,
    topic_id: str,
    difficulty: int = 1,
    answer_value: str = "42",
    answer_opt_id: str = "a",
) -> StoredQuestion:
    options = []
    if qtype == "mcq":
        options = [
            QuestionOption(id="a", text="Option A", misconception="Misconception A"),
            QuestionOption(id="b", text="Option B", misconception="Misconception B"),
        ]
        answer = QuestionAnswer(option_id=answer_opt_id)
    elif qtype == "numerical":
        answer = QuestionAnswer(value=answer_value)
    else:
        answer = QuestionAnswer(model_answer="Model answer")

    return StoredQuestion(
        type=qtype,
        topic_id=topic_id,
        difficulty=difficulty,
        stem=f"Stem for {qid}",
        options=options,
        answer=answer,
        rubric=[],
        explanation=f"Explanation for {qid}",
        citations=[
            Citation(
                chunk_id="chunk_1",
                loc=Location(source_id="src_1", page=1),
                label="p. 1",
                open=OpenPdfTarget(source_id="src_1", page=1),
            )
        ],
        verification=QuestionVerification(method="auto", passed=True, detail="ok"),
        status="verified",
        batch_id="batch_1",
        created_at=datetime.now(UTC),
    )


def test_selection_hand_worked_example_exact_match():
    """Verify hand-worked trace from plan gives exact output.

    Inputs:
      topic_ids: ["t2", "t3"], count: 5
      seen_ids: {"q_t2_mcq1", "q_t3_num1"}
      t2: q_t2_mcq1 (mcq, d1, seen), q_t2_mcq2 (mcq, d2, unseen),
          q_t2_num1 (num, d1, unseen), q_t2_num2 (num, d3, unseen)
      t3: q_t3_mcq1 (mcq, d1, unseen), q_t3_mcq2 (mcq, d2, unseen),
          q_t3_num1 (num, d1, seen), q_t3_num2 (num, d2, unseen)
    Expected:
      ["q_t2_mcq2", "q_t3_mcq1", "q_t2_num1", "q_t3_num2", "q_t2_num2"]
    """
    candidates = {
        "t2": [
            ("q_t2_mcq1", make_dummy_question("q_t2_mcq1", "mcq", "t2", 1)),
            ("q_t2_mcq2", make_dummy_question("q_t2_mcq2", "mcq", "t2", 2)),
            ("q_t2_num1", make_dummy_question("q_t2_num1", "numerical", "t2", 1, answer_value="1")),
            ("q_t2_num2", make_dummy_question("q_t2_num2", "numerical", "t2", 3, answer_value="2")),
        ],
        "t3": [
            ("q_t3_mcq1", make_dummy_question("q_t3_mcq1", "mcq", "t3", 1)),
            ("q_t3_mcq2", make_dummy_question("q_t3_mcq2", "mcq", "t3", 2)),
            ("q_t3_num1", make_dummy_question("q_t3_num1", "numerical", "t3", 1, answer_value="3")),
            ("q_t3_num2", make_dummy_question("q_t3_num2", "numerical", "t3", 2, answer_value="4")),
        ],
    }
    seen = {"q_t2_mcq1", "q_t3_num1"}
    selected = select_questions(candidates, ["t2", "t3"], seen, count=5)
    assert selected == ["q_t2_mcq2", "q_t3_mcq1", "q_t2_num1", "q_t3_num2", "q_t2_num2"]


def test_selection_unseen_before_seen():
    """Verify that all unseen questions are selected before seen questions are used."""
    candidates = {
        "t1": [
            ("q1_seen", make_dummy_question("q1_seen", "mcq", "t1", 1)),
            ("q2_unseen", make_dummy_question("q2_unseen", "mcq", "t1", 2)),
            ("q3_seen", make_dummy_question("q3_seen", "numerical", "t1", 1)),
            ("q4_unseen", make_dummy_question("q4_unseen", "numerical", "t1", 2)),
        ]
    }
    seen = {"q1_seen", "q3_seen"}
    # Request 2: should get only the 2 unseen questions
    selected_2 = select_questions(candidates, ["t1"], seen, count=2)
    assert set(selected_2) == {"q2_unseen", "q4_unseen"}

    # Request 4: should get the 2 unseen questions first, then backfill the 2 seen
    selected_4 = select_questions(candidates, ["t1"], seen, count=4)
    assert selected_4[:2] == ["q2_unseen", "q4_unseen"]
    assert set(selected_4[2:]) == {"q1_seen", "q3_seen"}


def test_selection_round_robin_and_type_balance():
    """Verify round-robin across topics and type alternation."""
    candidates = {
        "t1": [
            ("t1_m1", make_dummy_question("t1_m1", "mcq", "t1", 1)),
            ("t1_n1", make_dummy_question("t1_n1", "numerical", "t1", 1)),
        ],
        "t2": [
            ("t2_m1", make_dummy_question("t2_m1", "mcq", "t2", 1)),
            ("t2_n1", make_dummy_question("t2_n1", "numerical", "t2", 1)),
        ],
    }
    selected = select_questions(candidates, ["t1", "t2"], set(), count=4)
    # Round 1: t1 gives MCQ (t1_m1), t2 gives MCQ (t2_m1)
    # Round 2: t1 gives Num (t1_n1), t2 gives Num (t2_n1)
    assert selected == ["t1_m1", "t2_m1", "t1_n1", "t2_n1"]


def test_grade_input_validation():
    q_mcq = make_dummy_question("q_mcq", "mcq", "t1")
    with pytest.raises(QuizInputError, match="Enter an answer."):
        grade(q_mcq, "")
    with pytest.raises(QuizInputError, match="Enter an answer."):
        grade(q_mcq, "   ")
    with pytest.raises(QuizInputError, match="Answer must be at most 100 characters."):
        grade(q_mcq, "a" * 101)
    with pytest.raises(QuizInputError, match="Choose one of the options."):
        grade(q_mcq, "c")


def test_grade_mcq():
    q_mcq = make_dummy_question("q_mcq", "mcq", "t1", answer_opt_id="a")
    res_correct = grade(q_mcq, "a")
    assert res_correct.correct is True
    assert res_correct.verdict == "correct"
    assert res_correct.recorded_answer == "a"

    res_incorrect = grade(q_mcq, "b")
    assert res_incorrect.correct is False
    assert res_incorrect.verdict == "incorrect"
    assert res_incorrect.recorded_answer == "b"


def test_grade_numerical():
    q_num = make_dummy_question("q_num", "numerical", "t1", answer_value="3/8")
    assert grade(q_num, "3/8").correct is True
    assert grade(q_num, "0.375").correct is True
    assert grade(q_num, "0.3750").correct is True
    assert grade(q_num, "0.5").correct is False
    # Bad student input deterministically returns incorrect without raising
    assert grade(q_num, "not a number").correct is False
    assert grade(q_num, "abc/xyz").correct is False


def test_grade_numerical_bad_stored_answer_raises_value_error():
    q_bad = make_dummy_question("q_bad", "numerical", "t1", answer_value="42")
    q_bad.answer.value = "invalid"
    with pytest.raises(ValueError):
        grade(q_bad, "42")


def test_build_feedback_mcq():
    q_mcq = make_dummy_question("q_mcq", "mcq", "t1", answer_opt_id="a")
    # Correct attempt
    fb_corr = build_feedback(q_mcq, {"correct": True, "answer": "a"})
    assert fb_corr.verdict == "correct"
    assert fb_corr.correct_answer == "Option A"
    assert fb_corr.correct_option_id == "a"
    assert fb_corr.misconception is None
    assert len(fb_corr.citations) == 1

    # Incorrect attempt with misconception
    fb_incorr = build_feedback(q_mcq, {"correct": False, "answer": "b"})
    assert fb_incorr.verdict == "incorrect"
    assert fb_incorr.correct_answer == "Option A"
    assert fb_incorr.correct_option_id == "a"
    assert fb_incorr.misconception == "Misconception B"
