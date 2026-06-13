"""Safe evaluation of coupon eligibility expressions.

Offer conditions arrive as query strings inside menu payloads, e.g.::

    "subtotal >= 199"
    "user.member == true and subtotal >= 99"
    "select_subtotal >= 150"

Running these through ``eval()`` would execute arbitrary code from external
data. This module instead parses the expression with ``ast.parse`` and walks
the tree against an explicit whitelist of node types, so only simple
boolean/arithmetic expressions over the provided context can ever run.

Supported syntax:

* literals: numbers, strings, ``True``/``False``/``None`` plus the JSON
  spellings ``true``/``false``/``null``
* context lookups by name (``subtotal``) and dotted access into nested dicts
  (``user.member`` — dict key lookup, never ``getattr``)
* arithmetic ``+ - * /`` and unary ``+``/``-``
* comparisons ``< <= > >= == !=`` including chains, ``in``/``not in`` over
  list/tuple literals
* ``and`` / ``or`` / ``not`` with Python short-circuit semantics

Everything else — calls, subscripts, ``**``, comprehensions, attribute access
on anything that is not a dict — is rejected.
"""

from __future__ import annotations

import ast
import operator
from functools import lru_cache
from typing import Any, Mapping

__all__ = [
    "ExpressionError",
    "UnsafeExpressionError",
    "EvaluationError",
    "safe_eval",
    "validate_expression",
]

MAX_EXPRESSION_LENGTH = 500


class ExpressionError(ValueError):
    """Base class for all safe_eval failures."""


class UnsafeExpressionError(ExpressionError):
    """The expression cannot be accepted: syntax error or disallowed construct."""


class EvaluationError(ExpressionError):
    """The expression is well-formed but cannot be evaluated against this context."""


_JSON_LITERALS = {"true": True, "false": False, "null": None}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_CMP_OPS = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

# Full whitelist used by validate_expression (ast.walk yields operator and
# context nodes too, so they must all be listed).
_ALLOWED_NODE_TYPES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Compare,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
    ast.In,
    ast.NotIn,
    ast.List,
    ast.Tuple,
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@lru_cache(maxsize=1024)
def _parsed(expression: str) -> ast.Expression:
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise UnsafeExpressionError(
            f"expression longer than {MAX_EXPRESSION_LENGTH} characters"
        )
    if not expression.strip():
        raise UnsafeExpressionError("expression is empty")
    try:
        return ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise UnsafeExpressionError(f"invalid expression: {exc}") from exc


def safe_eval(expression: str, context: Mapping[str, Any]) -> Any:
    """Evaluate *expression* against *context* and return the result.

    Raises UnsafeExpressionError for unacceptable expressions and
    EvaluationError for well-formed expressions that fail against this
    context (unknown names, bad types, division by zero).
    """
    if not isinstance(expression, str):
        raise UnsafeExpressionError("expression must be a string")
    tree = _parsed(expression)
    try:
        return _eval_node(tree.body, context)
    except RecursionError as exc:
        raise UnsafeExpressionError("expression too deeply nested") from exc


def validate_expression(
    expression: str,
    allowed_names: frozenset[str],
    allowed_attributes: Mapping[str, frozenset[str]],
) -> None:
    """Check *expression* at parse time: only whitelisted syntax, only names in
    *allowed_names*, and attribute access only as ``base.field`` where *base*
    is a key of *allowed_attributes* and *field* one of its values.

    Lets menu loading fail fast on unsupported coupon queries instead of
    mis-optimizing later. Raises UnsafeExpressionError on any violation.
    """
    if not isinstance(expression, str):
        raise UnsafeExpressionError("expression must be a string")
    tree = _parsed(expression)
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise UnsafeExpressionError(
                f"{type(node).__name__} is not allowed in coupon queries"
            )
        if isinstance(node, ast.Name):
            if node.id not in _JSON_LITERALS and node.id not in allowed_names:
                raise UnsafeExpressionError(
                    f"unknown name {node.id!r}; allowed: {sorted(allowed_names)}"
                )
        elif isinstance(node, ast.Attribute):
            base = node.value
            if not (isinstance(base, ast.Name) and base.id in allowed_attributes):
                raise UnsafeExpressionError(
                    "attribute access is only allowed on: "
                    + ", ".join(sorted(allowed_attributes))
                )
            if node.attr not in allowed_attributes[base.id]:
                raise UnsafeExpressionError(
                    f"unknown field {base.id}.{node.attr}; allowed: "
                    + ", ".join(sorted(allowed_attributes[base.id]))
                )


def _eval_node(node: ast.AST, context: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        if node.value is None or isinstance(node.value, (bool, int, float, str)):
            return node.value
        raise UnsafeExpressionError(
            f"literal of type {type(node.value).__name__} is not allowed"
        )

    if isinstance(node, ast.Name):
        if node.id in _JSON_LITERALS:
            return _JSON_LITERALS[node.id]
        try:
            return context[node.id]
        except KeyError:
            raise EvaluationError(f"unknown name {node.id!r}") from None

    if isinstance(node, ast.Attribute):
        base = _eval_node(node.value, context)
        if isinstance(base, Mapping):
            try:
                return base[node.attr]
            except KeyError:
                raise EvaluationError(f"unknown attribute {node.attr!r}") from None
        raise EvaluationError(
            f"attribute access on {type(base).__name__} is not allowed"
        )

    if isinstance(node, ast.BoolOp):
        is_and = isinstance(node.op, ast.And)
        result: Any = None
        for sub in node.values:
            result = _eval_node(sub, context)
            if is_and and not result:
                return result
            if not is_and and result:
                return result
        return result

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_node(node.operand, context)
        if isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = _eval_node(node.operand, context)
            if not _is_number(operand):
                raise EvaluationError("unary +/- requires a number")
            return -operand if isinstance(node.op, ast.USub) else +operand
        raise UnsafeExpressionError(
            f"unary operator {type(node.op).__name__} is not allowed"
        )

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise UnsafeExpressionError(
                f"operator {type(node.op).__name__} is not allowed"
            )
        left = _eval_node(node.left, context)
        right = _eval_node(node.right, context)
        if not (_is_number(left) and _is_number(right)):
            raise EvaluationError("arithmetic requires numbers")
        try:
            return op(left, right)
        except ZeroDivisionError:
            raise EvaluationError("division by zero") from None

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op_node, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            if isinstance(op_node, (ast.In, ast.NotIn)):
                if not isinstance(right, (list, tuple)):
                    raise EvaluationError("`in` requires a list on the right")
                ok = left in right
                if isinstance(op_node, ast.NotIn):
                    ok = not ok
            else:
                op = _CMP_OPS.get(type(op_node))
                if op is None:
                    raise UnsafeExpressionError(
                        f"comparison {type(op_node).__name__} is not allowed"
                    )
                try:
                    ok = op(left, right)
                except TypeError:
                    raise EvaluationError(
                        f"cannot compare {type(left).__name__} "
                        f"and {type(right).__name__}"
                    ) from None
            if not ok:
                return False
            left = right
        return True

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(element, context) for element in node.elts]

    raise UnsafeExpressionError(f"{type(node).__name__} is not allowed")
