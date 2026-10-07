import os
import subprocess
import sys
import time
from fractions import Fraction

import pytest

from app.questions import numerical_sandbox
from app.questions.numerical import (
    SNIPPET_RULES,
    grade_numerical,
    parse_exact,
    verify_numerical,
)
from app.questions.numerical_sandbox import check_snippet, run_snippet


# --- Group 1: parse_exact ---
@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("3/8", Fraction(3, 8)),
        (" 3 / 8 ", Fraction(3, 8)),
        ("0.375", Fraction(3, 8)),
        (".5", Fraction(1, 2)),
        ("-2", Fraction(-2, 1)),
        ("+2", Fraction(2, 1)),
        ("−3/8", Fraction(-3, 8)),
        ("6/16", Fraction(3, 8)),
        ("0", Fraction(0, 1)),
        ("-0.25", Fraction(-1, 4)),
        ("-1.5", Fraction(-3, 2)),
        ("-.5", Fraction(-1, 2)),
        ("+1.25", Fraction(5, 4)),
    ],
)
def test_parse_exact_accepted(input_str: str, expected: Fraction):
    assert parse_exact(input_str) == expected


@pytest.mark.parametrize(
    "input_str",
    [
        "",
        "  ",
        "abc",
        "3/0",
        "3/-8",
        "1e3",
        "1_000",
        "inf",
        "nan",
        "0x10",
        "5.",
        "1.2.3",
        "3/8/2",
        "1 1/2",
        "37.5%",
        "2,598,960",
        "0.3/0.4",
        "3\n/8",
        "３/٨",  # Non-ASCII digits
    ],
)
def test_parse_exact_rejected(input_str: str):
    assert parse_exact(input_str) is None


def test_parse_exact_invalid_types_and_length():
    assert parse_exact(None) is None  # type: ignore[arg-type]
    assert parse_exact(123) is None  # type: ignore[arg-type]
    assert parse_exact([]) is None  # type: ignore[arg-type]
    assert parse_exact("1" * 101) is None


# --- Group 2: grade_numerical ---
@pytest.mark.parametrize(
    ("student", "correct"),
    [
        ("0.333", "1/3"),
        ("0.3333", "1/3"),
        ("33.3%", "1/3"),
        ("0.667", "2/3"),
        ("0.666", "2/3"),
        ("0.00452", "1/221"),
        ("0.004525", "1/221"),
        ("-0.333", "-1/3"),
        ("-1.5", "-3/2"),
        ("3/8", "3/8"),
        ("6/16", "3/8"),
        ("0.375", "3/8"),
        ("37.5%", "3/8"),
        ("37.5 %", "3/8"),
        ("2,598,960", "2598960"),
        ("2598960.0", "2598960"),
        ("-1/4", "-0.25"),
    ],
)
def test_grade_numerical_true(student: str, correct: str):
    assert grade_numerical(student, correct) is True


@pytest.mark.parametrize(
    ("student", "correct"),
    [
        ("0.33", "1/3"),
        ("0.3", "1/3"),
        ("33%", "1/3"),
        ("0.334", "1/3"),
        ("0.665", "2/3"),
        ("0.0045", "1/221"),
        ("", "3/8"),
        ("abc", "3/8"),
        ("3/8 or 1/2", "3/8"),
        ("1e-3", "1/1000"),
        ("2,59,8960", "2598960"),
        ("3/8%", "3/8"),
        ("0." + "3" * 200, "1/3"),
        ("３/８", "3/8"),
    ],
)
def test_grade_numerical_false(student: str, correct: str):
    assert grade_numerical(student, correct) is False


def test_grade_numerical_invalid_correct_answer_raises():
    with pytest.raises(ValueError):
        grade_numerical("1/3", "not_a_number")
    with pytest.raises(ValueError):
        grade_numerical("1/3", "3/0")
    with pytest.raises(ValueError):
        grade_numerical("1/3", "")


@pytest.mark.parametrize(
    "student_str",
    [
        "1.2.3",
        "0.3/0.4",
        "..",
        "1..2",
        "1.2.3%",
        ".",
        "%",
        "-",
        "+.",
        "1/",
        "/2",
        "1/2/3",
        "1,000.5.5",
        "1,,000",
        ",5",
        "5,",
        "1 000",
        "½",
        "1e5",
        "--1",
        "+-1",
        "0x1",
        "nan",
        "1/0",
        "1/0%",
        "37.5%%",
        "%37.5",
        "\u00a0",
    ],
)
def test_grade_numerical_invalid_student_input_returns_false_never_raises(student_str: str):
    assert grade_numerical(student_str, "1") is False


@pytest.mark.parametrize("student_val", [None, 5, 3.5, []])
def test_grade_numerical_non_str_returns_false(student_val: object):
    assert grade_numerical(student_val, "1") is False  # type: ignore[arg-type]


def test_grade_numerical_101_chars_returns_false():
    assert grade_numerical("1" * 101, "1") is False


def test_grade_numerical_fuzz():
    import random

    rng = random.Random(0)
    chars = "0123456789.,/%-+ "
    for _ in range(2000):
        length = rng.randint(0, 12)
        s = "".join(rng.choice(chars) for _ in range(length))
        res = grade_numerical(s, "3/8")
        assert isinstance(res, bool)


# --- Group 3: Hostile Static Snippets ---
HOSTILE_CASES = [
    ("import os\nanswer = 1", "imports are not allowed"),
    ("from os import system\nanswer = 1", "imports are not allowed"),
    ("x = __import__\nanswer = 1", "name '__import__' is not allowed"),
    ("x = ().__class__\nanswer = 1", "attribute access is not allowed"),
    ("x = Fraction(1, 3).numerator\nanswer = 1", "attribute access is not allowed"),
    ("x = open(1, 2)\nanswer = 1", "call to 'open' is not allowed"),
    ("x = eval(1)\nanswer = 1", "call to 'eval' is not allowed"),
    ("x = exec(1)\nanswer = 1", "call to 'exec' is not allowed"),
    ("x = getattr(1, 2)\nanswer = 1", "call to 'getattr' is not allowed"),
    ("x = print(1)\nanswer = 1", "call to 'print' is not allowed"),
    ("x = breakpoint()\nanswer = 1", "call to 'breakpoint' is not allowed"),
    ("f = lambda: 1\nanswer = 1", "lambda is not allowed"),
    ("def f(): pass\nanswer = 1", "function definitions are not allowed"),
    ("class A: pass\nanswer = 1", "class definitions are not allowed"),
    ("while True: pass\nanswer = 1", "while loops are not allowed"),
    ("try:\n    pass\nexcept Exception:\n    pass\nanswer = 1", "try blocks are not allowed"),
    ("with x:\n    pass\nanswer = 1", "with statements are not allowed"),
    ("raise\nanswer = 1", "raise statements are not allowed"),
    ("assert True\nanswer = 1", "assert statements are not allowed"),
    ("del x\nanswer = 1", "delete statements are not allowed"),
    ("global x\nanswer = 1", "global/nonlocal statements are not allowed"),
    ("x = (y := 1)\nanswer = 1", "walrus operator (:=) is not allowed"),
    ("x = f'{1}'\nanswer = 1", "f-strings are not allowed"),
    ("x = 's'\nanswer = 1", "string constants are not allowed"),
    ("x = 1.5\nanswer = 1", "float constants are not exact; use Fraction(a, b) or /"),
    ("x = 10000000000000000000\nanswer = 1", "integer constant is too large"),
    ("x = max(1, 2, key=abs)\nanswer = 1", "keyword arguments are not allowed"),
    ("x = sum(*[[1]])\nanswer = 1", "starred arguments are not allowed"),
    ("x = [1, 2][::2]\nanswer = 1", "slice indexing is not allowed"),
    ("_mul = 1\nanswer = 1", "name '_mul' is not allowed"),
    ("range = 5\nanswer = 1", "'range' is reserved and can't be assigned"),
    ("for comb in range(3):\n    pass\nanswer = 1", "'comb' is reserved and can't be assigned"),
    ("for i in range(3):\n    pass\nelse:\n    pass\nanswer = 1", "for-else is not allowed"),
    ("x = [" + "0, " * 101 + "]\nanswer = 1", "display has more than 100 elements"),
    (
        "x = [1 for a in range(1) for b in range(1) "
        "for c in range(1) for d in range(1)]\nanswer = 1",
        "comprehension has more than 3 for-clauses",
    ),
    ("1 + 1\nanswer = 1", "bare expressions are not allowed"),
    (
        "answer = 1\n# " + "a" * (2001 - len("answer = 1\n# ")),
        "code is longer than 2000 characters",
    ),
    ("answer = (", "syntax error:"),
    (None, "code must be a string"),
    ("x = 1", "no value is assigned to 'answer'"),
]


@pytest.mark.parametrize(("snippet", "expected_substring"), HOSTILE_CASES)
def test_check_snippet_and_verify_hostile_static(
    snippet: object, expected_substring: str, monkeypatch: pytest.MonkeyPatch
):
    reason = check_snippet(snippet)
    assert reason is not None
    assert expected_substring in reason

    def fail_subprocess(*args, **kwargs):
        pytest.fail("subprocess.run must not be called for statically rejected snippets")

    monkeypatch.setattr("app.questions.numerical.subprocess.run", fail_subprocess)

    result = verify_numerical(snippet, "1")  # type: ignore[arg-type]
    assert result.passed is False
    assert result.detail.startswith("rejected:")
    assert expected_substring in result.detail


def test_check_snippet_allowed_2000_char_snippet():
    prefix = "answer = 1\n# "
    code = prefix + "a" * (2000 - len(prefix))
    assert len(code) == 2000
    assert check_snippet(code) is None


# --- Group 4: Real 6.041-Style Problems ---
def test_verify_two_aces():
    code = "answer = comb(4, 2) / comb(52, 2)"
    res = verify_numerical(code, "1/221")
    assert res.passed is True
    assert res.computed == "1/221"
    assert res.detail.startswith("ok: computed 1/221 equals the stated answer")


def test_verify_bayes_radar():
    code = (
        "pa = Fraction(5, 100)\n"
        "pb_a = Fraction(99, 100)\n"
        "pb_na = Fraction(10, 100)\n"
        "answer = pa * pb_a / (pa * pb_a + (1 - pa) * pb_na)"
    )
    res = verify_numerical(code, "99/289")
    assert res.passed is True
    assert res.computed == "99/289"
    assert res.detail.startswith("ok:")


def test_verify_fair_die_expectation():
    code = "answer = sum(Fraction(k, 6) for k in range(1, 7))"
    res = verify_numerical(code, "7/2")
    assert res.passed is True
    assert res.computed == "7/2"
    assert res.detail.startswith("ok:")


def test_verify_two_dice_sum_7():
    code = (
        "count = 0\n"
        "for d1 in range(1, 7):\n"
        "    for d2 in range(1, 7):\n"
        "        if d1 + d2 == 7:\n"
        "            count += 1\n"
        "answer = count / 36"
    )
    res = verify_numerical(code, "1/6")
    assert res.passed is True
    assert res.computed == "1/6"
    assert res.detail.startswith("ok:")


def test_verify_binomial_heads():
    code = "answer = comb(5, 2) * Fraction(1, 2) ** 5"
    res = verify_numerical(code, "5/16")
    assert res.passed is True
    assert res.computed == "5/16"
    assert res.detail.startswith("ok:")


def test_verify_ordered_podium():
    code = "answer = perm(5, 3)"
    res = verify_numerical(code, "60")
    assert res.passed is True
    assert res.computed == "60"
    assert res.detail.startswith("ok:")


def test_verify_pmf_dict():
    code = (
        "pmf = {1: Fraction(1, 2), 2: Fraction(1, 4), 3: Fraction(1, 4)}\n"
        "answer = sum(k * pmf[k] for k in pmf)"
    )
    res = verify_numerical(code, "7/4")
    assert res.passed is True
    assert res.computed == "7/4"
    assert res.detail.startswith("ok:")


def test_verify_negative_power():
    code = "answer = 2 ** -3"
    res = verify_numerical(code, "0.125")
    assert res.passed is True
    assert res.computed == "1/8"
    assert res.detail.startswith("ok:")


def test_verify_real_subprocess_2000_char_snippet():
    prefix = "answer = 1\n# "
    code = prefix + "a" * (2000 - len(prefix))
    res = verify_numerical(code, "1")
    assert res.passed is True
    assert res.detail.startswith("ok:")


def test_verify_real_subprocess_2000_char_snippet_with_300_newlines():
    lines = ["#\n"] * 300
    base = "answer = 1\n" + "".join(lines)
    code = base + "# " + "a" * (2000 - len(base) - 2)
    assert len(code) == 2000
    assert code.count("\n") >= 300
    res = verify_numerical(code, "1")
    assert res.passed is True
    assert res.detail.startswith("ok:")


def test_verify_real_subprocess_crlf():
    code = "x = 1\r\nanswer = x"
    res = verify_numerical(code, "1")
    assert res.passed is True
    assert res.detail.startswith("ok:")


# --- Group 5: Mismatches and Bad Stated Answers ---
def test_verify_mismatch_value():
    code = "answer = comb(4, 2) / comb(52, 2)"
    res = verify_numerical(code, "1/220")
    assert res.passed is False
    assert res.computed == "1/221"
    assert res.detail.startswith("mismatch: computed 1/221, stated 1/220")


def test_verify_mismatch_rounded_stated():
    code = "answer = comb(4, 2) / comb(52, 2)"
    res = verify_numerical(code, "0.00452")
    assert res.passed is False
    assert res.computed == "1/221"
    assert res.detail.startswith("mismatch:")


def test_verify_bad_stated_answer_string(monkeypatch: pytest.MonkeyPatch):
    def fail_subprocess(*args, **kwargs):
        pytest.fail("subprocess must not be called")

    monkeypatch.setattr("app.questions.numerical.subprocess.run", fail_subprocess)
    res = verify_numerical("answer = 1", "1/221 approx")
    assert res.passed is False
    assert res.computed is None
    assert res.detail == "bad stated answer: '1/221 approx' is not an exact number"


def test_verify_bad_stated_answer_non_string(monkeypatch: pytest.MonkeyPatch):
    def fail_subprocess(*args, **kwargs):
        pytest.fail("subprocess must not be called")

    monkeypatch.setattr("app.questions.numerical.subprocess.run", fail_subprocess)
    res = verify_numerical("answer = 1", None)  # type: ignore[arg-type]
    assert res.passed is False
    assert res.computed is None
    assert res.detail == "bad stated answer: NoneType is not an exact number"


# --- Group 6: Runtime Errors, Limits & Memory Cap ---
def test_verify_runtime_zero_division():
    res = verify_numerical("answer = 1 / 0", "1")
    assert res.passed is False
    assert res.detail.startswith("error:")
    assert "ZeroDivisionError: division by zero" in res.detail


def test_verify_runtime_invalid_type_set():
    res = verify_numerical("answer = {1, 2}", "1")
    assert res.passed is False
    assert res.detail.startswith("error:")


def test_verify_runtime_invalid_type_bool():
    res = verify_numerical("answer = True", "1")
    assert res.passed is False
    assert res.detail.startswith("error:")


def test_verify_runtime_unassigned_answer():
    res = verify_numerical("if False:\n    answer = 1", "1")
    assert res.passed is False
    assert res.detail.startswith("error:")
    assert "did not set 'answer'" in res.detail


def test_verify_runtime_limit_huge_power():
    res = verify_numerical("answer = 10 ** 10 ** 10", "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")


def test_verify_runtime_limit_non_integer_power():
    res = verify_numerical("answer = 2 ** Fraction(1, 2)", "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")
    assert "whole number" in res.detail


def test_verify_runtime_limit_large_factorial():
    res = verify_numerical("answer = factorial(100000)", "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")


def test_verify_runtime_limit_large_comb():
    res = verify_numerical("answer = comb(10 ** 6, 3)", "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")


def test_verify_runtime_limit_large_range():
    res = verify_numerical("answer = len(range(10 ** 9))", "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")
    assert "range" in res.detail


def test_verify_runtime_limit_list_mul():
    res = verify_numerical("x = [0] * 1000\nanswer = 1", "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")


def test_verify_runtime_limit_list_add_doubling():
    code = "x = [0]\n" + ("x = x + x\n" * 40) + "answer = 1"
    res = verify_numerical(code, "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")


def test_verify_runtime_limit_int_mul_doubling():
    code = "x = 2\n" + ("x = x * x\n" * 40) + "answer = x"
    res = verify_numerical(code, "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")


def test_verify_runtime_limit_nested_loops_iterations():
    code = "answer = sum(1 for a in range(1000) for b in range(1000) for c in range(10))"
    res = verify_numerical(code, "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")
    assert "iteration" in res.detail


def test_verify_runtime_limit_sum_start_list():
    code = "answer = len(sum(([i] for i in range(10)), []))"
    res = verify_numerical(code, "1")
    assert res.passed is False
    assert res.detail.startswith("limit:")


def test_verify_memory_cap_exceeded():
    code = "x = 2 ** 10000\nL = [-x for k in range(1000000)]\nanswer = 1"
    res = verify_numerical(code, "1")
    assert res.passed is False
    assert res.detail.startswith("error:")
    assert "MemoryError" in res.detail


def test_verify_memory_cap_positive_control():
    code = "x = 2 ** 10000\nL = [-x for k in range(50000)]\nanswer = len(L)"
    res = verify_numerical(code, "50000")
    assert res.passed is True
    assert res.detail.startswith("ok:")
    assert res.computed == "50000"


# --- Group 7: Timeout ---
def test_verify_timeout():
    code = "answer = sum(Fraction(1, k) for k in range(1, 300000))"
    t0 = time.monotonic()
    res = verify_numerical(code, "1", timeout_s=1.0)
    elapsed = time.monotonic() - t0
    assert res.passed is False
    assert res.computed is None
    assert res.detail.startswith("timeout: no result within 1.0 s")
    assert elapsed < 6.0


# --- Group 8: In-Process run_snippet Tests ---
def test_run_snippet_division_exact():
    res = run_snippet("answer = 1 / 3")
    assert res == {"ok": True, "num": "1", "den": "3"}


def test_run_snippet_negative_exponent():
    res = run_snippet("answer = 2 ** -3")
    assert res == {"ok": True, "num": "1", "den": "8"}


def test_run_snippet_transform_nested_and_augassign():
    res1 = run_snippet("answer = 1 + 1 / 3")
    assert res1 == {"ok": True, "num": "4", "den": "3"}

    res2 = run_snippet("x = 1\nx /= 3\nanswer = x")
    assert res2 == {"ok": True, "num": "1", "den": "3"}

    res3 = run_snippet("answer = sum(k / 2 for k in range(3))")
    assert res3 == {"ok": True, "num": "3", "den": "2"}


def test_run_snippet_globals_isolation():
    res1 = run_snippet("answer = len(range(3))")
    assert res1 == {"ok": True, "num": "3", "den": "1"}

    res2 = run_snippet("x = open\nanswer = 1")
    assert res2["ok"] is False
    assert res2["kind"] == "error"
    assert "NameError" in res2["message"]


def test_run_snippet_error_formatting_huge_key():
    res = run_snippet("d = {}\nanswer = d[(2 ** 10000) ** 2]")
    assert res == {"ok": False, "kind": "error", "message": "KeyError: key not found"}


def test_run_snippet_max_exponent_bounds():
    res1 = run_snippet("x = 2 ** 10000\nanswer = 1")
    assert res1["ok"] is True

    res2 = run_snippet("x = 2 ** 10001\nanswer = 1")
    assert res2["ok"] is False
    assert res2["kind"] == "limit"

    res3 = run_snippet("x = 2 ** -10001\nanswer = 1")
    assert res3["ok"] is False
    assert res3["kind"] == "limit"


def test_run_snippet_range_overflow():
    res = run_snippet("answer = len(range(10 ** 18 * 10 ** 18))")
    assert res["ok"] is False
    assert res["kind"] == "limit"
    assert "range" in res["message"]


def test_run_snippet_zero_division():
    res = run_snippet("answer = 1 / 0")
    assert res == {
        "ok": False,
        "kind": "error",
        "message": "ZeroDivisionError: division by zero",
    }


def test_run_snippet_size_limit_on_add_and_div():
    code_add = "x = (2 ** 10000) ** 199\nanswer = Fraction(1, x) + Fraction(1, x + 1)"
    res_add = run_snippet(code_add)
    assert res_add["ok"] is False
    assert res_add["kind"] == "limit"

    code_div = "x = (2 ** 10000) ** 199\nanswer = Fraction(1, x) / (x + 1)"
    res_div = run_snippet(code_div)
    assert res_div["ok"] is False
    assert res_div["kind"] == "limit"


def test_run_snippet_iteration_budget_resets():
    code = "count = 0\nfor x in range(600000):\n    count += 1\nanswer = count"
    res1 = run_snippet(code)
    assert res1["ok"] is True
    res2 = run_snippet(code)
    assert res2["ok"] is True


# --- Group 9: Subprocess Command, Crash Path & Timeout Validation ---
def test_subprocess_command_structure(monkeypatch: pytest.MonkeyPatch):
    recorded_cmd = []

    real_run = subprocess.run

    def spy_run(cmd, *args, **kwargs):
        recorded_cmd.extend(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("app.questions.numerical.subprocess.run", spy_run)
    res = verify_numerical("answer = 1", "1")
    assert res.passed is True
    expected_interpreter = (
        sys._base_executable
        if hasattr(sys, "_base_executable") and os.path.isfile(sys._base_executable)
        else sys.executable
    )
    assert recorded_cmd[0] == expected_interpreter
    assert recorded_cmd[1:4] == ["-I", "-S", "-B"]
    assert recorded_cmd[4] == os.path.abspath(numerical_sandbox.__file__)


def test_verify_invalid_timeout_raises():
    with pytest.raises(ValueError):
        verify_numerical("answer = 1", "1", timeout_s=0)
    with pytest.raises(ValueError):
        verify_numerical("answer = 1", "1", timeout_s=31)
    with pytest.raises(ValueError):
        verify_numerical("answer = 1", "1", timeout_s=float("nan"))


@pytest.mark.parametrize(
    ("completed_proc", "expected_err"),
    [
        (subprocess.CompletedProcess([], 0, stdout=b"not json", stderr=b""), "invalid stdout"),
        (subprocess.CompletedProcess([], 0, stdout=b"[]", stderr=b""), "invalid json response"),
        (
            subprocess.CompletedProcess(
                [], 0, stdout=b'{"ok": true, "num": "x", "den": "1"}', stderr=b""
            ),
            "invalid num in response",
        ),
        (subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"boom"), "boom"),
        (subprocess.CompletedProcess([], 1, stdout=None, stderr=None), "exit code 1"),
        (
            subprocess.CompletedProcess(
                [],
                0,
                stdout=b'{"ok": true, "num": "' + b"1" * 5000 + b'", "den": "1"}',
                stderr=b"",
            ),
            "invalid num in response",
        ),
    ],
)
def test_verify_crash_paths(
    completed_proc: subprocess.CompletedProcess,
    expected_err: str,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.questions.numerical.subprocess.run", lambda *args, **kwargs: completed_proc
    )
    res = verify_numerical("answer = 1", "1")
    assert res.passed is False
    assert res.detail.startswith("crash:")
    assert expected_err in res.detail


# --- Group 10: SNIPPET_RULES ---
def test_snippet_rules_content():
    assert bool(SNIPPET_RULES)
    assert "answer" in SNIPPET_RULES
    assert "Fraction" in SNIPPET_RULES
    assert "comb" in SNIPPET_RULES
