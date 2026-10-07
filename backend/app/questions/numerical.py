import json
import os
import re
import subprocess
import sys
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel

from app.questions import numerical_sandbox
from app.questions.numerical_sandbox import check_snippet

SNIPPET_RULES: str = (
    "assign the final value to answer; "
    "use Fraction(a, b) or / for exact values (/ is exact here); "
    "allowed functions are Fraction, comb, perm, factorial, sum, min, max, abs, len, range; "
    "no imports, attribute access, strings, floats, def, lambda or while."
)

INTEGER_RE = re.compile(r"^[+-]?[0-9]+$")
FRACTION_RE = re.compile(r"^[+-]?[0-9]+[ \t]*/[ \t]*[0-9]+$")
DECIMAL_RE = re.compile(r"^[+-]?[0-9]*\.[0-9]+$")
STUDENT_PATTERN = re.compile(
    r"^(?P<sign>[+-])?"
    r"(?:"
    r"(?P<num>[0-9]+)[ \t]*/[ \t]*(?P<den>[0-9]+)"
    r"|"
    r"(?:"
    r"(?:"
    r"(?P<int_part>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.(?P<frac>[0-9]+))?"
    r"|"
    r"\.(?P<frac_only>[0-9]+)"
    r")"
    r"(?P<percent>[ \t]*%)?"
    r")"
    r")$"
)


class NumericalVerification(BaseModel):
    method: Literal["python"] = "python"
    passed: bool
    detail: str
    computed: str | None = None


def parse_exact(text: str) -> Fraction | None:
    if not isinstance(text, str):
        return None
    s = text.strip().replace("\u2212", "-")
    if not s or len(s) > 100:
        return None

    sign = -1 if s.startswith("-") else 1
    raw = s[1:] if s.startswith(("+", "-")) else s

    if INTEGER_RE.fullmatch(s):
        return Fraction(sign * int(raw), 1)

    if FRACTION_RE.fullmatch(s):
        parts = raw.split("/")
        den = int(parts[1].strip())
        if den == 0:
            return None
        num = int(parts[0].strip())
        return Fraction(sign * num, den)

    if DECIMAL_RE.fullmatch(s):
        int_str, frac_str = raw.split(".")
        int_val = int(int_str) if int_str else 0
        frac_val = int(frac_str)
        dec_places = len(frac_str)
        magnitude = int_val * (10**dec_places) + frac_val
        return Fraction(sign * magnitude, 10**dec_places)

    return None


def grade_numerical(student_answer: str, correct_answer: str) -> bool:
    correct = parse_exact(correct_answer)
    if correct is None:
        raise ValueError(f"Invalid correct_answer: {correct_answer!r}")

    if not isinstance(student_answer, str):
        return False
    s = student_answer.strip().replace("\u2212", "-")
    if not s or len(s) > 100:
        return False

    m = STUDENT_PATTERN.fullmatch(s)
    if m is None:
        return False

    sign = -1 if m.group("sign") == "-" else 1

    # Alternative A: fraction
    if m.group("num") is not None:
        den = int(m.group("den"))
        if den == 0:
            return False
        num = int(m.group("num"))
        return Fraction(sign * num, den) == correct

    # Alternative B: integer or decimal with optional percent
    is_percent = m.group("percent") is not None
    int_part = m.group("int_part")
    frac_str = m.group("frac") or m.group("frac_only")

    clean_int = int_part.replace(",", "") if int_part else "0"
    if frac_str is not None:
        d = len(frac_str)
        magnitude = int(clean_int) * (10**d) + int(frac_str)
        written_val = Fraction(sign * magnitude, 10**d)
    else:
        d = 0
        written_val = Fraction(sign * int(clean_int), 1)

    # Rule 1: exact match
    val = written_val * Fraction(1, 100) if is_percent else written_val
    if val == correct:
        return True

    # Rule 2: rounded decimal (only if decimal point is present)
    if frac_str is not None:
        raw_int = int_part.replace(",", "") if int_part else ""
        all_digits = (raw_int + frac_str).lstrip("0")
        s_figs = len(all_digits)
        if s_figs >= 3:
            target = correct * 100 if is_percent else correct
            scaled_target = target * (10**d)
            num = scaled_target.numerator
            den = scaled_target.denominator

            trunc_sign = -1 if num < 0 else 1
            trunc_int = trunc_sign * (abs(num) // den)
            truncated_frac = Fraction(trunc_int, 10**d)

            q = abs(num) // den
            r = abs(num) % den
            if r * 2 >= den:
                q += 1
            round_int = trunc_sign * q
            rounded_frac = Fraction(round_int, 10**d)

            if written_val == truncated_frac or written_val == rounded_frac:
                return True

    return False


def verify_numerical(
    solution_code: str, stated_answer: str, timeout_s: float = 2.0
) -> NumericalVerification:
    if not (0 < timeout_s <= 30):
        raise ValueError("timeout_s must be > 0 and <= 30")

    if not isinstance(stated_answer, str):
        tname = type(stated_answer).__name__
        return NumericalVerification(
            passed=False,
            detail=f"bad stated answer: {tname} is not an exact number"[:300],
            computed=None,
        )

    stated = parse_exact(stated_answer)
    if stated is None:
        return NumericalVerification(
            passed=False,
            detail=f"bad stated answer: '{stated_answer[:40]}' is not an exact number"[:300],
            computed=None,
        )

    reason = check_snippet(solution_code)
    if reason is not None:
        return NumericalVerification(
            passed=False, detail=f"rejected: {reason}"[:300], computed=None
        )

    # a venv launcher adds a second process; using the base interpreter keeps one process to kill
    interpreter = (
        sys._base_executable
        if hasattr(sys, "_base_executable") and os.path.isfile(sys._base_executable)
        else sys.executable
    )
    cmd = [
        interpreter,
        "-I",
        "-S",
        "-B",
        os.path.abspath(numerical_sandbox.__file__),
    ]

    try:
        r = subprocess.run(
            cmd,
            input=solution_code.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return NumericalVerification(
            passed=False,
            detail=f"timeout: no result within {timeout_s} s"[:300],
            computed=None,
        )
    except OSError as exc:
        return NumericalVerification(
            passed=False,
            detail=f"crash: {exc}"[:300],
            computed=None,
        )

    stdout = (r.stdout or b"").decode("utf-8", errors="replace")
    stderr = (r.stderr or b"").decode("utf-8", errors="replace")
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]

    if r.returncode != 0 or not lines:
        err = stderr.strip()[-200:] or f"exit code {r.returncode}"
        return NumericalVerification(
            passed=False,
            detail=f"crash: {err}"[:300],
            computed=None,
        )

    last_line = lines[-1]
    try:
        data = json.loads(last_line)
    except Exception:
        err = stderr.strip()[-200:] or "invalid stdout"
        return NumericalVerification(
            passed=False,
            detail=f"crash: {err}"[:300],
            computed=None,
        )

    if not isinstance(data, dict) or "ok" not in data or not isinstance(data["ok"], bool):
        err = stderr.strip()[-200:] or "invalid json response"
        return NumericalVerification(
            passed=False,
            detail=f"crash: {err}"[:300],
            computed=None,
        )

    if data["ok"] is True:
        num_str = data.get("num")
        den_str = data.get("den")
        if not (
            isinstance(num_str, str)
            and len(num_str) <= 1000
            and re.fullmatch(r"^-?[0-9]+$", num_str)
        ):
            err = stderr.strip()[-200:] or "invalid num in response"
            return NumericalVerification(
                passed=False, detail=f"crash: {err}"[:300], computed=None
            )
        if not (
            isinstance(den_str, str)
            and len(den_str) <= 1000
            and re.fullmatch(r"^[0-9]+$", den_str)
            and int(den_str) != 0
        ):
            err = stderr.strip()[-200:] or "invalid den in response"
            return NumericalVerification(
                passed=False, detail=f"crash: {err}"[:300], computed=None
            )

        num = int(num_str)
        den = int(den_str)
        computed_frac = Fraction(num, den)
        computed_str = f"{num}" if den == 1 else f"{num}/{den}"

        if computed_frac == stated:
            return NumericalVerification(
                passed=True,
                detail=f"ok: computed {computed_str} equals the stated answer"[:300],
                computed=computed_str,
            )
        else:
            stated_str = (
                f"{stated.numerator}"
                if stated.denominator == 1
                else f"{stated.numerator}/{stated.denominator}"
            )
            return NumericalVerification(
                passed=False,
                detail=f"mismatch: computed {computed_str}, stated {stated_str}"[:300],
                computed=computed_str,
            )

    kind = data.get("kind")
    msg = data.get("message")
    if kind not in {"rejected", "limit", "error"} or not isinstance(msg, str):
        err = stderr.strip()[-200:] or "invalid failure response"
        return NumericalVerification(
            passed=False, detail=f"crash: {err}"[:300], computed=None
        )

    return NumericalVerification(
        passed=False, detail=f"{kind}: {msg}"[:300], computed=None
    )
