def user_path(uid: str) -> str:
    """Return Firestore document path for a user."""
    return f"users/{uid}"


def notebooks_collection_path() -> str:
    """Return Firestore collection path for notebooks."""
    return "notebooks"


def notebook_path(nb: str) -> str:
    """Return Firestore document path for a notebook."""
    return f"notebooks/{nb}"


def sources_collection_path(nb: str) -> str:
    """Return Firestore collection path for a notebook's sources."""
    return f"notebooks/{nb}/sources"


def source_path(nb: str, src: str) -> str:
    """Return Firestore document path for a source."""
    return f"notebooks/{nb}/sources/{src}"


def chunks_collection_path(nb: str) -> str:
    """Return Firestore collection path for a notebook's chunks."""
    return f"notebooks/{nb}/chunks"


def chunk_path(nb: str, chunk_id: str) -> str:
    """Return Firestore document path for a chunk."""
    return f"notebooks/{nb}/chunks/{chunk_id}"


def source_storage_original_pdf_path(nb: str, src: str) -> str:
    """Return Cloud Storage object path for a source's original PDF."""
    return f"notebooks/{nb}/sources/{src}/original.pdf"


def llm_cache_collection_path() -> str:
    """Return Firestore collection path for LLM cache."""
    return "llm_cache"


def llm_cache_path(key: str) -> str:
    """Return Firestore document path for an LLM cache entry."""
    return f"llm_cache/{key}"


def topics_collection_path(nb: str) -> str:
    """Return Firestore collection path for a notebook's topics."""
    return f"notebooks/{nb}/topics"


def topic_path(nb: str, topic_id: str) -> str:
    """Return Firestore document path for a topic."""
    return f"notebooks/{nb}/topics/{topic_id}"

def questions_collection_path(nb: str) -> str:
    """Return Firestore collection path for a notebook's questions."""
    return f"notebooks/{nb}/questions"


def question_path(nb: str, q: str) -> str:
    """Return Firestore document path for a question."""
    return f"notebooks/{nb}/questions/{q}"


def member_path(nb: str, uid: str) -> str:
    """Return Firestore document path for a notebook member."""
    return f"notebooks/{nb}/members/{uid}"


def quizzes_collection_path(nb: str, uid: str) -> str:
    """Return Firestore collection path for a member's quizzes."""
    return f"notebooks/{nb}/members/{uid}/quizzes"


def quiz_path(nb: str, uid: str, quiz: str) -> str:
    """Return Firestore document path for a quiz."""
    return f"notebooks/{nb}/members/{uid}/quizzes/{quiz}"


def attempts_collection_path(nb: str, uid: str) -> str:
    """Return Firestore collection path for a member's attempts."""
    return f"notebooks/{nb}/members/{uid}/attempts"


def attempt_path(nb: str, uid: str, attempt: str) -> str:
    """Return Firestore document path for an attempt."""
    return f"notebooks/{nb}/members/{uid}/attempts/{attempt}"


def chats_collection_path(nb: str, uid: str) -> str:
    """Return Firestore collection path for a member's chats."""
    return f"notebooks/{nb}/members/{uid}/chats"


def chat_path(nb: str, uid: str, chat_id: str) -> str:
    """Return Firestore document path for a member's chat."""
    return f"notebooks/{nb}/members/{uid}/chats/{chat_id}"


def messages_collection_path(nb: str, uid: str, chat_id: str) -> str:
    """Return Firestore collection path for a chat's messages."""
    return f"notebooks/{nb}/members/{uid}/chats/{chat_id}/messages"


def message_path(nb: str, uid: str, chat_id: str, msg_id: str) -> str:
    """Return Firestore document path for a chat message."""
    return f"notebooks/{nb}/members/{uid}/chats/{chat_id}/messages/{msg_id}"


def mastery_collection_path(nb: str, uid: str) -> str:
    """Return Firestore collection path for a member's topic mastery."""
    return f"notebooks/{nb}/members/{uid}/mastery"


def mastery_path(nb: str, uid: str, topic_id: str) -> str:
    """Return Firestore document path for a member's topic mastery."""
    return f"notebooks/{nb}/members/{uid}/mastery/{topic_id}"


def coach_events_collection_path(nb: str, uid: str) -> str:
    """Return Firestore collection path for a member's coach events."""
    return f"notebooks/{nb}/members/{uid}/coach_events"


def coach_event_path(nb: str, uid: str, event_id: str) -> str:
    """Return Firestore document path for a member's coach event."""
    return f"notebooks/{nb}/members/{uid}/coach_events/{event_id}"

