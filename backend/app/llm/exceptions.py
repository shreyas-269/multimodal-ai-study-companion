class QuotaExhausted(Exception):
    """Raised when Gemini returns 429 / RESOURCE_EXHAUSTED."""

    def __init__(self, retry_after_s: int):
        self.retry_after_s = retry_after_s
        msg = f'The AI model\'s free quota is used up. Try again in {retry_after_s} seconds.'
        super().__init__(msg)


class UnreadableOutput(Exception):
    """Raised when Gemini returns None, invalid JSON, or schema validation fails twice."""

    pass


class ModelUnavailable(Exception):
    """Raised when the AI model is temporarily unavailable after retries."""

    pass
