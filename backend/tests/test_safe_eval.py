"""Unit tests for the enhanced safe expression evaluator (V0.9).

Tests both the original V0.8 functionality (comparisons, booleans, arithmetic)
and the V0.9 additions (whitelisted function calls and method calls).
"""

import pytest
from app.engine.safe_eval import safe_eval, SafeEvalError


# ---------------------------------------------------------------------------
# Existing V0.8 functionality (regression tests)
# ---------------------------------------------------------------------------

class TestBasicExpressions:
    def test_literal_comparison(self):
        assert safe_eval('1 == 1', {}) is True

    def test_variable_lookup(self):
        assert safe_eval('x > 5', {'x': 10}) is True
        assert safe_eval('x > 5', {'x': 3}) is False

    def test_boolean_true_false_keywords(self):
        assert safe_eval('true', {}) is True
        assert safe_eval('false', {}) is False

    def test_and_or_not(self):
        assert safe_eval('true and true', {}) is True
        assert safe_eval('true and false', {}) is False
        assert safe_eval('false or true', {}) is True
        assert safe_eval('not false', {}) is True

    def test_arithmetic(self):
        assert safe_eval('2 + 3', {}) == 5
        assert safe_eval('10 - 4', {}) == 6
        assert safe_eval('3 * 7', {}) == 21

    def test_dict_attribute_access(self):
        env = {'output': {'status': 'done'}}
        assert safe_eval('output.status == "done"', env) is True

    def test_dict_subscript_access(self):
        env = {'data': {'items': [1, 2, 3]}}
        assert safe_eval('data["items"]', env) == [1, 2, 3]

    def test_in_operator(self):
        env = {'status': 'critical'}
        assert safe_eval('status in ["high", "critical"]', env) is True
        assert safe_eval('status in ["low", "medium"]', env) is False

    def test_list_literal(self):
        assert safe_eval('[1, 2, 3]', {}) == [1, 2, 3]

    def test_ternary_expression(self):
        assert safe_eval('"yes" if true else "no"', {}) == "yes"
        assert safe_eval('"yes" if false else "no"', {}) == "no"

    def test_unknown_variable_raises(self):
        with pytest.raises(SafeEvalError, match="Unknown variable"):
            safe_eval('unknown_var', {})

    def test_invalid_syntax_raises(self):
        with pytest.raises(SafeEvalError, match="Invalid expression syntax"):
            safe_eval('if x:', {})


# ---------------------------------------------------------------------------
# V0.9: Whitelisted function calls
# ---------------------------------------------------------------------------

class TestWhitelistedFunctions:
    def test_len(self):
        assert safe_eval('len(items) > 0', {'items': [1, 2, 3]}) is True
        assert safe_eval('len(items)', {'items': []}) == 0

    def test_str(self):
        assert safe_eval('str(42)', {}) == "42"

    def test_int(self):
        assert safe_eval('int("42")', {}) == 42

    def test_float(self):
        assert safe_eval('float("3.14")', {}) == 3.14

    def test_bool(self):
        assert safe_eval('bool(1)', {}) is True
        assert safe_eval('bool(0)', {}) is False

    def test_abs(self):
        assert safe_eval('abs(-5)', {}) == 5

    def test_min_max(self):
        assert safe_eval('min(3, 7)', {}) == 3
        assert safe_eval('max(3, 7)', {}) == 7

    def test_lower_upper(self):
        assert safe_eval('lower("HELLO")', {}) == "hello"
        assert safe_eval('upper("hello")', {}) == "HELLO"

    def test_strip(self):
        assert safe_eval('strip("  hello  ")', {}) == "hello"

    def test_startswith_endswith(self):
        assert safe_eval('startswith("hello world", "hello")', {}) is True
        assert safe_eval('endswith("hello world", "world")', {}) is True
        assert safe_eval('startswith("hello world", "world")', {}) is False

    def test_contains(self):
        assert safe_eval('contains("hello world", "world")', {}) is True
        assert safe_eval('contains("hello world", "xyz")', {}) is False

    def test_matches(self):
        assert safe_eval('matches("test123", r"[a-z]+\\d+")', {}) is True
        assert safe_eval('matches("TEST123", r"[a-z]+\\d+")', {}) is False

    def test_matches_invalid_regex(self):
        with pytest.raises(SafeEvalError, match="Invalid regex"):
            safe_eval('matches("test", r"[invalid")', {})


# ---------------------------------------------------------------------------
# V0.9: Method calls
# ---------------------------------------------------------------------------

class TestMethodCalls:
    def test_string_lower(self):
        assert safe_eval('name.lower()', {'name': 'ALICE'}) == 'alice'

    def test_string_upper(self):
        assert safe_eval('name.upper()', {'name': 'alice'}) == 'ALICE'

    def test_string_startswith(self):
        assert safe_eval('name.startswith("Al")', {'name': 'Alice'}) is True

    def test_string_strip(self):
        assert safe_eval('name.strip()', {'name': '  Bob  '}) == 'Bob'

    def test_string_split(self):
        assert safe_eval('name.split(",")', {'name': 'a,b,c'}) == ['a', 'b', 'c']

    def test_dict_get(self):
        env = {'data': {'key': 'value'}}
        assert safe_eval('data.get("key")', env) == 'value'

    def test_dict_keys(self):
        env = {'data': {'a': 1, 'b': 2}}
        result = safe_eval('data.keys()', env)
        assert set(result) == {'a', 'b'}

    def test_string_isdigit(self):
        assert safe_eval('val.isdigit()', {'val': '123'}) is True
        assert safe_eval('val.isdigit()', {'val': 'abc'}) is False


# ---------------------------------------------------------------------------
# V0.9: Security — disallowed calls
# ---------------------------------------------------------------------------

class TestDisallowedCalls:
    def test_import_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval('__import__("os")', {})

    def test_eval_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval('eval("1+1")', {})

    def test_exec_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval('exec("print(1)")', {})

    def test_open_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval('open("/etc/passwd")', {})

    def test_disallowed_method(self):
        with pytest.raises(SafeEvalError, match="not allowed"):
            safe_eval('name.__class__()', {'name': 'test'})

    def test_compile_blocked(self):
        with pytest.raises(SafeEvalError):
            safe_eval('compile("print(1)", "", "exec")', {})


# ---------------------------------------------------------------------------
# V0.9: Complex expressions combining functions with logic
# ---------------------------------------------------------------------------

class TestComplexExpressions:
    def test_len_with_comparison(self):
        env = {'output': {'node_1': {'items': [1, 2, 3]}}}
        assert safe_eval('len(output.node_1.items) >= 3', env) is True

    def test_lower_with_comparison(self):
        env = {'trigger': {'status': 'ACTIVE'}}
        assert safe_eval('lower(trigger.status) == "active"', env) is True

    def test_method_in_boolean_expression(self):
        env = {'name': 'admin_user'}
        assert safe_eval('name.startswith("admin") and len(name) > 5', env) is True

    def test_nested_function_and_method(self):
        env = {'items': [' Alice ', ' Bob ']}
        assert safe_eval('len(items) == 2', env) is True
