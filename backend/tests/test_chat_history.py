from app.chat.history import (
    build_retrieval_query,
    format_chat_history,
    render_turn,
)


def test_build_retrieval_query_no_previous():
    """3.1 previous_user_text=None returns raw question."""
    q = "What is Bayes theorem?"
    assert build_retrieval_query(q, None) == q


def test_build_retrieval_query_with_previous():
    """3.2 Question comes first with history."""
    q = "Explain that again"
    prev = "What is Bayes theorem?"
    expected = f"{q}\n{prev}"
    assert build_retrieval_query(q, prev) == expected


def test_build_retrieval_query_truncation_at_500():
    """3.3 Truncation at 500 characters for previous user text."""
    q = "Follow up question"
    long_prev = "x" * 800
    res = build_retrieval_query(q, long_prev)
    assert res == f"{q}\n{'x' * 500}"
    assert len(res) == len(q) + 1 + 500


def test_render_turn_user_and_assistant():
    """4.2 Role prefixes for Student and Tutor."""
    user_doc = {"role": "user", "text": "Can you explain prior probabilities?"}
    assert render_turn(user_doc) == "Student: Can you explain prior probabilities?"

    asst_doc = {
        "role": "assistant",
        "paragraphs": [
            {"text": "A prior probability is an initial belief.", "outside_course": False},
            {"text": "It gets updated with evidence.", "outside_course": False},
        ],
    }
    expected = (
        "Tutor: A prior probability is an initial belief.\n\n"
        "It gets updated with evidence."
    )
    assert render_turn(asst_doc) == expected


def test_render_turn_outside_course_prefix():
    """4.3 Paragraphs with outside_course=True are prefixed with [Not from the course]."""
    asst_doc = {
        "role": "assistant",
        "paragraphs": [
            {"text": "Course material fact.", "outside_course": False},
            {"text": "General real-world analogy.", "outside_course": True},
        ],
    }
    rendered = render_turn(asst_doc)
    assert "[Not from the course] General real-world analogy." in rendered
    assert "[Not from the course] Course material fact." not in rendered


def test_render_turn_1500_char_cut_with_ellipsis():
    """4.4 Turn > 1,500 chars is cut to exactly 1,500 chars ending with ellipsis."""
    long_text = "A" * 2000
    user_doc = {"role": "user", "text": long_text}
    rendered = render_turn(user_doc)
    assert len(rendered) == 1500
    assert rendered.endswith("\u2026")
    assert not rendered.endswith("?")
    assert rendered[-1] != "?"
    # Prefix "Student: " (9 chars) + 1490 "A"s + "\u2026"
    assert rendered[:1499] == f"Student: {'A' * 1490}"


def test_format_chat_history():
    """4.2 and 4.6 Multiple turns joined by double newlines, empty history returns None."""
    assert format_chat_history([]) is None

    messages = [
        {"role": "user", "text": "Hi"},
        {"role": "assistant", "paragraphs": [{"text": "Hello!"}]},
    ]
    formatted = format_chat_history(messages)
    assert formatted == "Student: Hi\n\nTutor: Hello!"
