"""Tests for the sandboxed coupon-query evaluator."""

import pytest

from cart_optimizer.safe_eval import (
    EvaluationError,
    UnsafeExpressionError,
    safe_eval,
    validate_expression,
)

CTX = {
    "subtotal": 250,
    "select_subtotal": 120,
    "user": {"member": True, "first_order": False},
}

ALLOWED_NAMES = frozenset({"subtotal", "select_subtotal", "user"})
ALLOWED_ATTRS = {"user": frozenset({"member", "first_order"})}


# --- literals ---------------------------------------------------------------

@pytest.mark.parametrize(
    "expr,expected",
    [
        ("199", 199),
        ("12.5", 12.5),
        ("'abc'", "abc"),
        ("True", True),
        ("true", True),       # JSON spelling
        ("false", False),
        ("None", None),
        ("null", None),       # JSON spelling
    ],
)
def test_literals(expr, expected):
    assert safe_eval(expr, {}) == expected


# --- names and attributes ----------------------------------------------------

def test_context_name():
    assert safe_eval("subtotal", CTX) == 250


def test_unknown_name_raises():
    with pytest.raises(EvaluationError):
        safe_eval("delivery", CTX)


def test_dotted_lookup_into_dict():
    assert safe_eval("user.member", CTX) is True
    assert safe_eval("user.first_order", CTX) is False


def test_missing_dict_key_raises():
    with pytest.raises(EvaluationError):
        safe_eval("user.age", CTX)


def test_attribute_on_non_mapping_raises_without_leaking():
    # must NOT fall back to getattr — ints have real attributes
    with pytest.raises(EvaluationError):
        safe_eval("subtotal.real", CTX)


# --- comparisons --------------------------------------------------------------

@pytest.mark.parametrize(
    "expr,expected",
    [
        ("subtotal >= 199", True),
        ("subtotal < 199", False),
        ("subtotal == 250", True),
        ("subtotal != 250", False),
        ("100 <= select_subtotal <= 150", True),   # chained
        ("100 <= subtotal <= 150", False),
        ("user.member == true", True),
        ("user.first_order != true", True),
    ],
)
def test_comparisons(expr, expected):
    assert safe_eval(expr, CTX) is expected


def test_incomparable_types_raise():
    with pytest.raises(EvaluationError):
        safe_eval("subtotal < 'abc'", CTX)


# --- boolean logic -------------------------------------------------------------

def test_and_or_not():
    assert safe_eval("subtotal >= 199 and user.member", CTX) is True
    assert safe_eval("subtotal > 500 or user.member", CTX) is True
    assert safe_eval("not user.first_order", CTX) is True


def test_short_circuit_skips_unevaluated_operands():
    # Python semantics: the right side is never evaluated, so the unknown
    # name does not raise.
    assert safe_eval("false and missing_name", CTX) is False
    assert safe_eval("true or missing_name", CTX) is True


# --- arithmetic -----------------------------------------------------------------

def test_arithmetic():
    assert safe_eval("subtotal - 50 >= 200", CTX) is True
    assert safe_eval("subtotal * 2 == 500", CTX) is True
    assert safe_eval("10 / 4", CTX) == 2.5


def test_division_by_zero_raises():
    with pytest.raises(EvaluationError):
        safe_eval("subtotal / 0", CTX)


def test_arithmetic_rejects_non_numbers():
    with pytest.raises(EvaluationError):
        safe_eval("'a' + 'b'", CTX)


# --- membership -----------------------------------------------------------------

def test_in_and_not_in():
    assert safe_eval("subtotal in [250, 300]", CTX) is True
    assert safe_eval("subtotal not in [1, 2]", CTX) is True


def test_in_requires_list_on_right():
    with pytest.raises(EvaluationError):
        safe_eval("subtotal in 5", CTX)


# --- unsafe constructs ------------------------------------------------------------

@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('true')",  # call
        "subtotal[0]",                      # subscript
        "lambda: 1",                        # lambda
        "2 ** 10",                          # pow (DoS vector)
        "f(1)",                             # any call
        "[x for x in []]",                  # comprehension
        "subtotal >=",                      # syntax error
        "",                                 # empty
        "   ",                              # blank
    ],
)
def test_unsafe_expressions_rejected(expr):
    with pytest.raises(UnsafeExpressionError):
        safe_eval(expr, CTX)


def test_dunder_probing_cannot_reach_real_attributes():
    # tuple literal is allowed, but attribute access on it must fail safely
    with pytest.raises(EvaluationError):
        safe_eval("().__class__", CTX)


def test_overlong_expression_rejected():
    expr = "1 + " * 200 + "1"
    with pytest.raises(UnsafeExpressionError):
        safe_eval(expr, CTX)


# --- validate_expression (parse-time vocabulary check) ------------------------------

def test_validate_accepts_supported_vocabulary():
    validate_expression(
        "subtotal >= 199 and user.member == true", ALLOWED_NAMES, ALLOWED_ATTRS
    )
    validate_expression("select_subtotal >= 150", ALLOWED_NAMES, ALLOWED_ATTRS)


def test_validate_rejects_unknown_name():
    with pytest.raises(UnsafeExpressionError):
        validate_expression("item_count >= 2", ALLOWED_NAMES, ALLOWED_ATTRS)


def test_validate_rejects_unknown_user_field():
    with pytest.raises(UnsafeExpressionError):
        validate_expression("user.age == 1", ALLOWED_NAMES, ALLOWED_ATTRS)


def test_validate_rejects_attribute_on_non_user():
    with pytest.raises(UnsafeExpressionError):
        validate_expression("subtotal.x == 1", ALLOWED_NAMES, ALLOWED_ATTRS)


def test_validate_rejects_calls():
    with pytest.raises(UnsafeExpressionError):
        validate_expression("f(subtotal)", ALLOWED_NAMES, ALLOWED_ATTRS)
