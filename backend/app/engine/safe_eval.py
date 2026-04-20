"""Safe expression evaluator for condition nodes.

Replaces Python's built-in eval() with a restricted AST-walking evaluator
that only allows comparison, boolean, and arithmetic operations on data
from the execution context.  Function calls are permitted only for an
explicit whitelist of safe built-in functions and string methods.

Supported syntax examples:
    context.node_1.status == "completed"
    output.node_2.score > 0.8
    output.node_1.count >= 5 and output.node_2.active == true
    trigger.priority in ["high", "critical"]
    not output.node_3.error
    len(output.node_1.items) > 0
    lower(trigger.status) == "active"
    matches(trigger.email, r"^[a-z]+@example\\.com$")
"""

from __future__ import annotations

import ast
import logging
import operator
import re
from typing import Any

from app.engine.expression_helpers import EXPRESSION_HELPERS, ExpressionHelperError

logger = logging.getLogger(__name__)

_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}

_BOOL_OPS = {
    ast.And: all,
    ast.Or: any,
}

_UNARY_OPS = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


# ---------------------------------------------------------------------------
# Whitelisted functions for safe_eval condition expressions
# ---------------------------------------------------------------------------

def _safe_matches(text: str, pattern: str) -> bool:
    """Regex full-match with a 1-second timeout guard."""
    try:
        return re.fullmatch(pattern, str(text)) is not None
    except re.error as exc:
        raise SafeEvalError(f"Invalid regex pattern: {exc}") from exc


def _safe_contains(haystack, needle) -> bool:
    """Check if needle is in haystack (safe 'in' wrapper)."""
    return needle in haystack


_WHITELISTED_FUNCTIONS: dict[str, Any] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "abs": abs,
    "min": min,
    "max": max,
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "strip": lambda s: str(s).strip(),
    "startswith": lambda s, prefix: str(s).startswith(str(prefix)),
    "endswith": lambda s, suffix: str(s).endswith(str(suffix)),
    "contains": _safe_contains,
    "matches": _safe_matches,
    # DV-04 — bulk helpers (string / math / array / object / date / util).
    # Defined in expression_helpers.py and merged here so safe_eval has
    # a single whitelist lookup. Any name overlap lets the DV-04 entry win;
    # for now nothing overlaps.
    **EXPRESSION_HELPERS,
}

# String methods allowed via obj.method() syntax
_WHITELISTED_METHODS: set[str] = {
    "lower", "upper", "strip", "lstrip", "rstrip",
    "startswith", "endswith", "replace", "split",
    "join", "count", "find", "rfind", "isdigit", "isalpha",
    "get", "keys", "values", "items",
}


class SafeEvalError(Exception):
    """Raised when the expression contains disallowed syntax."""


def safe_eval(expr: str, variables: dict[str, Any]) -> Any:
    """Evaluate a restricted expression string against the given variables.

    Only literals, name lookups, attribute access on variable values,
    subscript access, comparisons, boolean ops, and basic arithmetic
    are allowed.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise SafeEvalError(f"Invalid expression syntax: {exc}") from exc

    return _eval_node(tree.body, variables)


def _eval_node(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id == "none" or node.id == "None":
            return None
        if node.id not in env:
            raise SafeEvalError(f"Unknown variable: {node.id}")
        return env[node.id]

    if isinstance(node, ast.Attribute):
        val = _eval_node(node.value, env)
        if isinstance(val, dict):
            return val.get(node.attr)
        raise SafeEvalError(f"Attribute access not allowed on {type(val).__name__}")

    if isinstance(node, ast.Subscript):
        val = _eval_node(node.value, env)
        key = _eval_node(node.slice, env)
        if isinstance(val, (dict, list)):
            try:
                return val[key]
            except (KeyError, IndexError, TypeError):
                return None
        raise SafeEvalError(f"Subscript access not allowed on {type(val).__name__}")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env)
        for op_node, comparator in zip(node.ops, node.comparators):
            op_func = _COMPARE_OPS.get(type(op_node))
            if not op_func:
                raise SafeEvalError(f"Unsupported comparison: {type(op_node).__name__}")
            right = _eval_node(comparator, env)
            if not op_func(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        op_func = _BOOL_OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Unsupported boolean op: {type(node.op).__name__}")
        values = [_eval_node(v, env) for v in node.values]
        return op_func(values)

    if isinstance(node, ast.UnaryOp):
        op_func = _UNARY_OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Unsupported unary op: {type(node.op).__name__}")
        return op_func(_eval_node(node.operand, env))

    if isinstance(node, ast.BinOp):
        op_func = _BIN_OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Unsupported binary op: {type(node.op).__name__}")
        return op_func(_eval_node(node.left, env), _eval_node(node.right, env))

    if isinstance(node, ast.List):
        return [_eval_node(el, env) for el in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(el, env) for el in node.elts)

    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, env): _eval_node(v, env)
            for k, v in zip(node.keys, node.values)
        }

    if isinstance(node, ast.IfExp):
        test = _eval_node(node.test, env)
        return _eval_node(node.body, env) if test else _eval_node(node.orelse, env)

    # ── Whitelisted function and method calls ──
    if isinstance(node, ast.Call):
        args = [_eval_node(a, env) for a in node.args]

        # Case 1: top-level function call — e.g. len(x), lower(x), matches(x, y)
        if isinstance(node.func, ast.Name):
            fn_name = node.func.id
            fn = _WHITELISTED_FUNCTIONS.get(fn_name)
            if fn is None:
                raise SafeEvalError(
                    f"Function '{fn_name}' is not allowed. "
                    f"Allowed: {', '.join(sorted(_WHITELISTED_FUNCTIONS))}"
                )
            try:
                return fn(*args)
            except ExpressionHelperError as exc:
                # Translate helper-level errors into the SafeEvalError
                # contract so every caller can keep a single except clause.
                raise SafeEvalError(f"{fn_name}(): {exc}") from exc

        # Case 2: method call — e.g. x.lower(), x.startswith("abc")
        if isinstance(node.func, ast.Attribute):
            obj = _eval_node(node.func.value, env)
            method_name = node.func.attr
            if method_name not in _WHITELISTED_METHODS:
                raise SafeEvalError(
                    f"Method '.{method_name}()' is not allowed. "
                    f"Allowed: {', '.join(sorted(_WHITELISTED_METHODS))}"
                )
            method = getattr(obj, method_name, None)
            if method is None or not callable(method):
                raise SafeEvalError(
                    f"Object of type {type(obj).__name__} has no method '{method_name}'"
                )
            return method(*args)

        raise SafeEvalError("Only named function calls and method calls are allowed.")

    raise SafeEvalError(
        f"Disallowed expression node: {type(node).__name__}. "
        "Only comparisons, boolean ops, arithmetic, function calls, and variable lookups are allowed."
    )
