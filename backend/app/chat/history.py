from typing import Any

CUT_MARKER = "\u2026"


def build_retrieval_query(
    question: str,
    previous_user_text: str | None,
) -> str:
    """Build retrieval query placing current question first.

    Followed by previous user text (up to 500 chars).
    """
    if not previous_user_text:
        return question
    return f"{question}\n{previous_user_text[:500]}"


def render_turn(doc_data: dict[str, Any]) -> str:
    """Render a single message turn, cut to 1,500 chars. Returns raw text."""
    role = doc_data.get("role", "user")
    if role == "user":
        text = doc_data.get("text") or ""
        rendered = f"Student: {text}"
    else:
        paragraphs = doc_data.get("paragraphs") or []
        p_texts = []
        for p in paragraphs:
            p_text = p.get("text", "")
            if p.get("outside_course"):
                p_text = f"[Not from the course] {p_text}"
            p_texts.append(p_text)
        rendered = f"Tutor: {'\n\n'.join(p_texts)}"

    # Cut first (exactly 1,500 chars with the ellipsis); the builder neutralises later.
    if len(rendered) > 1500:
        rendered = rendered[:1499] + CUT_MARKER

    return rendered


def format_chat_history(messages: list[dict[str, Any]]) -> str | None:
    """Format list of stored message dicts (oldest first) into conversation text block."""
    if not messages:
        return None
    turns = [render_turn(m) for m in messages]
    return "\n\n".join(turns)
