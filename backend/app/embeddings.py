import threading
from pathlib import Path

from fastembed import TextEmbedding
from tokenizers import Tokenizer

MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
BACKEND_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = (BACKEND_DIR / ".cache" / "fastembed").resolve()

_model_lock = threading.RLock()
_model_instance: TextEmbedding | None = None
_tokenizer_instance: Tokenizer | None = None


def get_model() -> TextEmbedding:
    """Lazily load the FastEmbed model singleton in a thread-safe manner."""
    global _model_instance
    if _model_instance is None:
        with _model_lock:
            if _model_instance is None:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _model_instance = TextEmbedding(
                    model_name=MODEL_NAME,
                    cache_dir=str(CACHE_DIR),
                )
    return _model_instance


def get_tokenizer() -> Tokenizer:
    """Lazily load the model's tokenizer with truncation and padding disabled."""
    global _tokenizer_instance
    if _tokenizer_instance is None:
        with _model_lock:
            if _tokenizer_instance is None:
                get_model()  # Ensure model assets are downloaded to CACHE_DIR
                tokenizer_paths = list(CACHE_DIR.rglob("tokenizer.json"))
                if not tokenizer_paths:
                    raise FileNotFoundError(f"tokenizer.json not found under {CACHE_DIR}")
                tok = Tokenizer.from_file(str(tokenizer_paths[0]))
                tok.no_truncation()
                tok.no_padding()
                _tokenizer_instance = tok
    return _tokenizer_instance


def count_tokens(text: str) -> int:
    """Count tokens in text using the model's native tokenizer without truncation."""
    tokenizer = get_tokenizer()
    return len(tokenizer.encode(text).ids)


def embed_passages(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Generate 384-dimensional embeddings for passages without instruction prefix."""
    if not texts:
        return []
    model = get_model()
    embeddings = model.embed(texts, batch_size=batch_size)
    return [emb.tolist() for emb in embeddings]


def embed_query(text: str) -> list[float]:
    """Generate 384-dimensional query embedding with the BGE query instruction applied once."""
    model = get_model()
    if text.startswith(QUERY_INSTRUCTION):
        query_text = text
    else:
        query_text = f"{QUERY_INSTRUCTION}{text}"
    embeddings = list(model.embed([query_text]))
    return embeddings[0].tolist()
