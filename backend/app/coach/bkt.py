"""Bayesian Knowledge Tracing (BKT) constants and pure mathematical functions."""

PRIOR: float = 0.3
GUESS: dict[str, float] = {"mcq": 0.25, "short": 0.1, "numerical": 0.05}
SLIP: float = 0.1
LEARN: float = 0.15
CHECKBOX_FLOOR: float = 0.8
FORGET_HALF_LIFE_DAYS: float = 14.0
CORRECT_THRESHOLD: float = 0.5
MASTERED_THRESHOLD: float = 0.7


def posterior(p: float, correct: bool, guess: float) -> float:
    if correct:
        num = p * (1.0 - SLIP)
        den = num + (1.0 - p) * guess
    else:
        num = p * SLIP
        den = num + (1.0 - p) * (1.0 - guess)
    return num / den


def learn(p: float) -> float:
    return p + (1.0 - p) * LEARN


def update(p: float, correct: bool, question_type: str) -> float:
    post = posterior(p, correct, GUESS[question_type])
    learned = learn(post)
    return max(0.0, min(1.0, learned))


def decay(p: float, elapsed_days: float) -> float:
    if elapsed_days <= 0.0 or p <= PRIOR:
        return p
    return PRIOR + (p - PRIOR) * (0.5 ** (elapsed_days / FORGET_HALF_LIFE_DAYS))


def is_correct(score: float) -> bool:
    return score >= CORRECT_THRESHOLD


def label(p_effective: float, has_record: bool) -> str:
    if not has_record:
        return "not_started"
    if p_effective >= MASTERED_THRESHOLD:
        return "mastered"
    return "learning"
