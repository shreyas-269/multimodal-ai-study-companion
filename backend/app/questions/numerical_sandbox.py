import ast
import fractions
import json
import math
import sys

MAX_CODE_LEN: int = 2000
MAX_AST_NODES: int = 1000
MAX_INT_VALUE: int = 10**18
MAX_DISPLAY_ELEMENTS: int = 100
MAX_COMPREHENSION_FOR_CLAUSES: int = 3
MAX_EXPONENT: int = 10_000
MAX_COMB_N: int = 10_000
MAX_RANGE_LEN: int = 1_000_000
MAX_ITERATION_BUDGET: int = 1_000_000
MAX_BIT_SIZE: int = 2_000_000
MAX_ANSWER_BITS: int = 3_000

RESERVED_NAMES: frozenset[str] = frozenset(
    {"Fraction", "comb", "perm", "factorial", "sum", "min", "max", "abs", "len", "range"}
)

ALLOWED_NODE_TYPES: tuple[type, ...] = (
    ast.Module,
    ast.Assign,
    ast.AugAssign,
    ast.For,
    ast.If,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.Name,
    ast.Constant,
    ast.Tuple,
    ast.List,
    ast.Set,
    ast.Dict,
    ast.Subscript,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Load,
    ast.Store,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.BitAnd,
    ast.BitOr,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)

ALLOWED_BINOP_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.BitAnd,
    ast.BitOr,
)

ALLOWED_AUGASSIGN_OPS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
)

ALLOWED_UNARY_OPS = (
    ast.USub,
    ast.UAdd,
    ast.Not,
)

ALLOWED_BOOL_OPS = (
    ast.And,
    ast.Or,
)

ALLOWED_COMPARE_OPS = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
)


class SandboxLimit(Exception):
    """Raised when a snippet exceeds execution, memory, or size budgets."""


def _is_number(val: object) -> bool:
    return isinstance(val, (int, fractions.Fraction))


def _bit_size(val: object) -> int:
    if isinstance(val, bool):
        return 1
    if isinstance(val, int):
        return val.bit_length()
    if isinstance(val, fractions.Fraction):
        return val.numerator.bit_length() + val.denominator.bit_length()
    return 0


def _add(a: object, b: object) -> fractions.Fraction | int:
    if not (_is_number(a) and _is_number(b)):
        raise SandboxLimit("'+' operands must both be numbers")
    res = a + b  # type: ignore[operator]
    if _bit_size(res) > MAX_BIT_SIZE:
        raise SandboxLimit("addition result would be too large")
    return res


def _mul(a: object, b: object) -> fractions.Fraction | int:
    if not (_is_number(a) and _is_number(b)):
        raise SandboxLimit("'*' operands must both be numbers")
    if _bit_size(a) + _bit_size(b) > MAX_BIT_SIZE:
        raise SandboxLimit("multiplication result would be too large")
    return a * b  # type: ignore[operator]


def _div(a: object, b: object) -> fractions.Fraction:
    if not (_is_number(a) and _is_number(b)):
        raise SandboxLimit("'/' operands must both be numbers")
    if b == 0:
        raise ZeroDivisionError("division by zero")
    res = fractions.Fraction(a, b)  # type: ignore[arg-type]
    if _bit_size(res) > MAX_BIT_SIZE:
        raise SandboxLimit("division result would be too large")
    return res


def _pow(a: object, b: object) -> fractions.Fraction | int:
    if not _is_number(a):
        raise SandboxLimit("'**' base must be a number")
    if isinstance(b, fractions.Fraction):
        if b.denominator != 1:
            raise SandboxLimit("exponent must be a whole number")
        b_val = b.numerator
    elif isinstance(b, int):  # bool counts as int
        b_val = int(b)
    else:
        raise SandboxLimit("exponent must be a whole number")

    if abs(b_val) > MAX_EXPONENT:
        raise SandboxLimit("exponent is too large")
    if _bit_size(a) * abs(b_val) > MAX_BIT_SIZE:
        raise SandboxLimit("power result would be too large")
    if b_val < 0:
        return fractions.Fraction(1, a ** abs(b_val))  # type: ignore[operator]
    return a**b_val  # type: ignore[operator]


def _guarded_sum(iterable: object, start: object = 0, /) -> fractions.Fraction | int:
    if not _is_number(start):
        raise SandboxLimit("'sum' start must be a number")
    total: fractions.Fraction | int = start  # type: ignore[assignment]
    for item in iterable:  # type: ignore[union-attr]
        if not _is_number(item):
            raise SandboxLimit("'sum' items must be numbers")
        total = _add(total, item)
    return total


def _guarded_comb(n: object, k: object, /) -> int:
    if not (isinstance(n, int) and isinstance(k, int)):
        raise SandboxLimit("'comb' arguments must be integers")
    if not (0 <= n <= MAX_COMB_N and 0 <= k <= MAX_COMB_N):
        raise SandboxLimit("'comb' arguments must be between 0 and 10000")
    return math.comb(n, k)


def _guarded_perm(n: object, k: object = None, /) -> int:
    if not isinstance(n, int):
        raise SandboxLimit("'perm' argument must be an integer")
    if not (0 <= n <= MAX_COMB_N):
        raise SandboxLimit("'perm' argument must be between 0 and 10000")
    if k is not None:
        if not isinstance(k, int):
            raise SandboxLimit("'perm' argument must be an integer")
        if not (0 <= k <= MAX_COMB_N):
            raise SandboxLimit("'perm' argument must be between 0 and 10000")
        return math.perm(n, k)
    return math.perm(n)


def _guarded_factorial(n: object, /) -> int:
    if not isinstance(n, int):
        raise SandboxLimit("'factorial' argument must be an integer")
    if not (0 <= n <= MAX_COMB_N):
        raise SandboxLimit("'factorial' argument must be between 0 and 10000")
    return math.factorial(n)


def _guarded_range(*args: object) -> range:
    for a in args:
        if not isinstance(a, int):
            raise SandboxLimit("'range' arguments must be integers")
    r = range(*args)  # type: ignore[arg-type]
    try:
        r_len = len(r)
    except OverflowError as exc:
        raise SandboxLimit("'range' length exceeds 1000000") from exc
    if r_len > MAX_RANGE_LEN:
        raise SandboxLimit("'range' length exceeds 1000000")
    return r


def _format_error(exc: Exception) -> str:
    if isinstance(exc, KeyError):
        return "KeyError: key not found"
    try:
        msg = str(exc).strip()
        if not msg:
            return type(exc).__name__[:200]
        return f"{type(exc).__name__}: {msg}"[:200]
    except Exception:
        return type(exc).__name__[:200]


def check_snippet(code: object) -> str | None:
    if not isinstance(code, str):
        return "code must be a string"
    if not code.strip():
        return "code is empty"
    if len(code) > MAX_CODE_LEN:
        return "code is longer than 2000 characters"

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return f"syntax error: {exc}"

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        return "code contains more than 1000 AST nodes"

    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "imports are not allowed"
        if isinstance(node, ast.Attribute):
            return "attribute access is not allowed"
        if isinstance(node, ast.Lambda):
            return "lambda is not allowed"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return "function definitions are not allowed"
        if isinstance(node, ast.ClassDef):
            return "class definitions are not allowed"
        if isinstance(node, ast.While):
            return "while loops are not allowed"
        if isinstance(node, (ast.With, getattr(ast, "AsyncWith", ()))):
            return "with statements are not allowed"
        if isinstance(node, (ast.Try, getattr(ast, "TryStar", ()))):
            return "try blocks are not allowed"
        if isinstance(node, ast.Raise):
            return "raise statements are not allowed"
        if isinstance(node, ast.Assert):
            return "assert statements are not allowed"
        if isinstance(node, ast.Delete):
            return "delete statements are not allowed"
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return "global/nonlocal statements are not allowed"
        if isinstance(node, ast.NamedExpr):
            return "walrus operator (:=) is not allowed"
        if isinstance(node, ast.Starred):
            return "starred expressions are not allowed"
        if isinstance(node, ast.Slice):
            return "slice syntax is not allowed"
        if isinstance(node, (ast.JoinedStr, ast.FormattedValue)):
            return "f-strings are not allowed"
        if isinstance(node, ast.Expr):
            return "bare expressions are not allowed"
        if isinstance(node, ast.keyword):
            return "keyword arguments are not allowed"
        if not isinstance(node, ALLOWED_NODE_TYPES):
            return f"'{type(node).__name__}' is not allowed"

        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, ALLOWED_BINOP_OPS):
                return f"operator '{type(node.op).__name__}' is not allowed"

        elif isinstance(node, ast.AugAssign):
            if not isinstance(node.target, ast.Name):
                return "AugAssign target must be a simple variable"
            if not isinstance(node.op, ALLOWED_AUGASSIGN_OPS):
                return f"operator '{type(node.op).__name__}' is not allowed"

        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, ALLOWED_UNARY_OPS):
                return f"unary operator '{type(node.op).__name__}' is not allowed"

        elif isinstance(node, ast.BoolOp):
            if not isinstance(node.op, ALLOWED_BOOL_OPS):
                return f"boolean operator '{type(node.op).__name__}' is not allowed"

        elif isinstance(node, ast.Compare):
            for op in node.ops:
                if not isinstance(op, ALLOWED_COMPARE_OPS):
                    return f"comparison operator '{type(op).__name__}' is not allowed"

        elif isinstance(node, ast.Constant):
            if isinstance(node.value, float):
                return "float constants are not exact; use Fraction(a, b) or /"
            if isinstance(node.value, str):
                return "string constants are not allowed"
            if not isinstance(node.value, (int, bool)):
                return f"constant of type '{type(node.value).__name__}' is not allowed"
            if isinstance(node.value, int) and not isinstance(node.value, bool):
                if abs(node.value) > MAX_INT_VALUE:
                    return "integer constant is too large"

        elif isinstance(node, ast.Name):
            if node.id.startswith("_"):
                return f"name '{node.id}' is not allowed"
            if isinstance(node.ctx, ast.Store) and node.id in RESERVED_NAMES:
                return f"'{node.id}' is reserved and can't be assigned"

        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                return "call target must be a function name"
            if node.func.id not in RESERVED_NAMES:
                return f"call to '{node.func.id}' is not allowed"
            if node.keywords:
                return "keyword arguments are not allowed"
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                return "starred arguments are not allowed"

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    continue
                if isinstance(target, ast.Tuple) and all(
                    isinstance(elt, ast.Name) for elt in target.elts
                ):
                    continue
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    if not isinstance(target.slice, ast.Slice):
                        continue
                return "invalid assignment target"

        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                pass
            elif isinstance(node.target, ast.Tuple) and all(
                isinstance(elt, ast.Name) for elt in node.target.elts
            ):
                pass
            else:
                return "invalid loop target"
            if node.orelse:
                return "for-else is not allowed"

        elif isinstance(node, ast.comprehension):
            if isinstance(node.target, ast.Name):
                pass
            elif isinstance(node.target, ast.Tuple) and all(
                isinstance(elt, ast.Name) for elt in node.target.elts
            ):
                pass
            else:
                return "invalid comprehension target"
            if getattr(node, "is_async", 0):
                return "async comprehensions are not allowed"

        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            if len(node.generators) > MAX_COMPREHENSION_FOR_CLAUSES:
                return "comprehension has more than 3 for-clauses"

        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            if len(node.elts) > MAX_DISPLAY_ELEMENTS:
                return "display has more than 100 elements"

        elif isinstance(node, ast.Dict):
            if len(node.keys) > MAX_DISPLAY_ELEMENTS:
                return "dict display has more than 100 elements"
            if any(k is None for k in node.keys):
                return "dict unpacking (**) is not allowed"

        elif isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Slice):
                return "slice indexing is not allowed"

    # Verify at least one assignment targets the name 'answer'
    has_answer_target = False
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "answer":
                    has_answer_target = True
                    break
                if isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name) and elt.id == "answer":
                            has_answer_target = True
                            break
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "answer":
                has_answer_target = True
                break
        if has_answer_target:
            break

    if not has_answer_target:
        return "no value is assigned to 'answer'"

    return None


class _SandboxTransformer(ast.NodeTransformer):
    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.Add):
            return ast.Call(
                func=ast.Name(id="_add", ctx=ast.Load()),
                args=[node.left, node.right],
                keywords=[],
            )
        if isinstance(node.op, ast.Mult):
            return ast.Call(
                func=ast.Name(id="_mul", ctx=ast.Load()),
                args=[node.left, node.right],
                keywords=[],
            )
        if isinstance(node.op, ast.Div):
            return ast.Call(
                func=ast.Name(id="_div", ctx=ast.Load()),
                args=[node.left, node.right],
                keywords=[],
            )
        if isinstance(node.op, ast.Pow):
            return ast.Call(
                func=ast.Name(id="_pow", ctx=ast.Load()),
                args=[node.left, node.right],
                keywords=[],
            )
        return node

    def visit_AugAssign(self, node: ast.AugAssign) -> ast.AST:
        self.generic_visit(node)
        op_func: str | None = None
        if isinstance(node.op, ast.Add):
            op_func = "_add"
        elif isinstance(node.op, ast.Mult):
            op_func = "_mul"
        elif isinstance(node.op, ast.Div):
            op_func = "_div"
        elif isinstance(node.op, ast.Pow):
            op_func = "_pow"

        if op_func is not None and isinstance(node.target, ast.Name):
            load_target = ast.Name(id=node.target.id, ctx=ast.Load())
            call = ast.Call(
                func=ast.Name(id=op_func, ctx=ast.Load()),
                args=[load_target, node.value],
                keywords=[],
            )
            return ast.Assign(targets=[node.target], value=call)
        return node

    def visit_For(self, node: ast.For) -> ast.AST:
        self.generic_visit(node)
        node.iter = ast.Call(
            func=ast.Name(id="_it", ctx=ast.Load()),
            args=[node.iter],
            keywords=[],
        )
        return node

    def visit_comprehension(self, node: ast.comprehension) -> ast.AST:
        self.generic_visit(node)
        node.iter = ast.Call(
            func=ast.Name(id="_it", ctx=ast.Load()),
            args=[node.iter],
            keywords=[],
        )
        return node


def _apply_memory_cap() -> None:
    if sys.platform == "win32":
        import ctypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            err = ctypes.get_last_error()
            raise RuntimeError(f"CreateJobObjectW failed: error {err}")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = 512 * 1024 * 1024

        ok = kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            err = ctypes.get_last_error()
            raise RuntimeError(f"SetInformationJobObject failed: error {err}")

        ok = kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())
        if not ok:
            err = ctypes.get_last_error()
            raise RuntimeError(f"AssignProcessToJobObject failed: error {err}")
    else:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        cap = 512 * 1024 * 1024
        if hard != getattr(resource, "RLIM_INFINITY", -1) and hard < cap:
            cap = hard
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def run_snippet(code: str) -> dict:
    try:
        check = check_snippet(code)
        if check is not None:
            return {"ok": False, "kind": "rejected", "message": check[:200]}

        tree = ast.parse(code)
        transformer = _SandboxTransformer()
        transformed_tree = transformer.visit(tree)
        ast.fix_missing_locations(transformed_tree)
        code_obj = compile(transformed_tree, "<snippet>", "exec")

        iteration_counter = [0]

        def _it_gen(iterable: object) -> object:
            for item in iterable:  # type: ignore[union-attr]
                iteration_counter[0] += 1
                if iteration_counter[0] > MAX_ITERATION_BUDGET:
                    raise SandboxLimit("iteration budget exceeded (max 1000000 iterations)")
                yield item

        g = {
            "__builtins__": {
                "abs": abs,
                "len": len,
                "min": min,
                "max": max,
            },
            "Fraction": fractions.Fraction,
            "sum": _guarded_sum,
            "comb": _guarded_comb,
            "perm": _guarded_perm,
            "factorial": _guarded_factorial,
            "range": _guarded_range,
            "_add": _add,
            "_mul": _mul,
            "_div": _div,
            "_pow": _pow,
            "_it": _it_gen,
        }

        exec(code_obj, g)

        if "answer" not in g:
            return {"ok": False, "kind": "error", "message": "snippet did not set 'answer'"}

        ans = g["answer"]
        if isinstance(ans, bool) or not isinstance(ans, (int, fractions.Fraction)):
            return {
                "ok": False,
                "kind": "error",
                "message": f"answer is {type(ans).__name__}, not an exact number",
            }

        if isinstance(ans, int):
            if ans.bit_length() > MAX_ANSWER_BITS:
                return {"ok": False, "kind": "limit", "message": "answer is too large"}
            return {"ok": True, "num": str(ans), "den": "1"}
        else:
            if (
                ans.numerator.bit_length() > MAX_ANSWER_BITS
                or ans.denominator.bit_length() > MAX_ANSWER_BITS
            ):
                return {"ok": False, "kind": "limit", "message": "answer is too large"}
            return {"ok": True, "num": str(ans.numerator), "den": str(ans.denominator)}

    except SandboxLimit as exc:
        return {"ok": False, "kind": "limit", "message": str(exc)[:200]}
    except Exception as exc:
        return {"ok": False, "kind": "error", "message": _format_error(exc)}


def main() -> None:
    try:
        _apply_memory_cap()
    except Exception as exc:
        msg = f"memory cap could not be applied: {exc}"[:200]
        sys.stdout.write(json.dumps({"ok": False, "kind": "error", "message": msg}) + "\n")
        sys.stdout.flush()
        sys.exit(0)

    code = sys.stdin.buffer.read().decode("utf-8")
    res = run_snippet(code)
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
