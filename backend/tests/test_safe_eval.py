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


# ---------------------------------------------------------------------------
# DV-04: Extended arithmetic (power, floor div)
# ---------------------------------------------------------------------------

class TestExtendedArithmetic:
    def test_pow(self):
        assert safe_eval('2 ** 10', {}) == 1024

    def test_floor_div(self):
        assert safe_eval('10 // 3', {}) == 3


# ---------------------------------------------------------------------------
# DV-04: String helpers
# ---------------------------------------------------------------------------

class TestStringHelpers:
    def test_trim(self):
        assert safe_eval('trim("  hi  ")', {}) == "hi"

    def test_replace(self):
        assert safe_eval('replace("foo-bar", "-", "_")', {}) == "foo_bar"

    def test_split_join(self):
        assert safe_eval('split("a,b,c", ",")', {}) == ["a", "b", "c"]
        assert safe_eval('join("-", ["a", "b", "c"])', {}) == "a-b-c"

    def test_substring(self):
        assert safe_eval('substring("hello", 1, 4)', {}) == "ell"
        assert safe_eval('substring("hello", 2)', {}) == "llo"

    def test_left_right(self):
        assert safe_eval('left("hello", 3)', {}) == "hel"
        assert safe_eval('right("hello", 2)', {}) == "lo"
        assert safe_eval('left("hi", 0)', {}) == ""
        assert safe_eval('right("hi", -1)', {}) == ""

    def test_pad(self):
        assert safe_eval('pad_left("42", 5, "0")', {}) == "00042"
        assert safe_eval('pad_right("x", 3, ".")', {}) == "x.."

    def test_repeat(self):
        assert safe_eval('repeat("ab", 3)', {}) == "ababab"

    def test_repeat_cap(self):
        with pytest.raises(SafeEvalError, match="repeat"):
            safe_eval('repeat("x", 20000)', {})

    def test_repeat_negative(self):
        with pytest.raises(SafeEvalError, match="repeat"):
            safe_eval('repeat("x", -1)', {})

    def test_reverse_string(self):
        assert safe_eval('reverse("abc")', {}) == "cba"

    def test_reverse_list(self):
        assert safe_eval('reverse([1, 2, 3])', {}) == [3, 2, 1]

    def test_truncate(self):
        assert safe_eval('truncate("hello world", 8)', {}) == "hello w…"
        # Exact-fit returns unchanged.
        assert safe_eval('truncate("hello", 5)', {}) == "hello"
        # Suffix longer than cap → hard cut.
        assert safe_eval('truncate("hello", 2, "...")', {}) == "he"

    def test_title_case(self):
        assert safe_eval('title_case("hello world")', {}) == "Hello World"

    def test_snake_case(self):
        assert safe_eval('snake_case("helloWorld")', {}) == "hello_world"
        assert safe_eval('snake_case("Hello  World!")', {}) == "hello_world"

    def test_camel_case(self):
        assert safe_eval('camel_case("hello_world")', {}) == "helloWorld"
        assert safe_eval('camel_case("Hello world")', {}) == "helloWorld"

    def test_regex_replace(self):
        assert safe_eval('regex_replace("abc123", r"\\d+", "N")', {}) == "abcN"

    def test_regex_replace_invalid(self):
        with pytest.raises(SafeEvalError, match="Invalid regex"):
            safe_eval('regex_replace("x", r"[", "")', {})

    def test_regex_extract_group(self):
        assert safe_eval('regex_extract("order-42", r"(\\d+)")', {}) == "42"

    def test_regex_extract_no_group(self):
        assert safe_eval('regex_extract("abc123", r"\\d+")', {}) == "123"

    def test_regex_extract_no_match(self):
        assert safe_eval('regex_extract("abc", r"\\d+")', {}) is None

    def test_slugify(self):
        assert safe_eval('slugify("Hello, World!")', {}) == "hello-world"
        assert safe_eval('slugify("  Foo   Bar  ")', {}) == "foo-bar"


# ---------------------------------------------------------------------------
# DV-04: Math helpers
# ---------------------------------------------------------------------------

class TestMathHelpers:
    def test_round(self):
        assert safe_eval('round(3.14159, 2)', {}) == 3.14
        assert safe_eval('round(2.5)', {}) in (2, 3)  # banker's rounding

    def test_ceil_floor(self):
        assert safe_eval('ceil(1.2)', {}) == 2
        assert safe_eval('floor(1.9)', {}) == 1

    def test_sum_int(self):
        assert safe_eval('sum([1, 2, 3])', {}) == 6

    def test_sum_float(self):
        assert safe_eval('sum([0.1, 0.2])', {}) == pytest.approx(0.3)

    def test_avg(self):
        assert safe_eval('avg([10, 20, 30])', {}) == 20.0

    def test_avg_empty_raises(self):
        with pytest.raises(SafeEvalError, match="avg"):
            safe_eval('avg([])', {})

    def test_median(self):
        assert safe_eval('median([1, 2, 3, 4, 5])', {}) == 3

    def test_clamp(self):
        assert safe_eval('clamp(15, 0, 10)', {}) == 10
        assert safe_eval('clamp(-5, 0, 10)', {}) == 0
        assert safe_eval('clamp(5, 0, 10)', {}) == 5

    def test_clamp_invalid(self):
        with pytest.raises(SafeEvalError, match="clamp"):
            safe_eval('clamp(5, 10, 0)', {})


# ---------------------------------------------------------------------------
# DV-04: Array helpers
# ---------------------------------------------------------------------------

class TestArrayHelpers:
    def test_first_last(self):
        assert safe_eval('first([10, 20, 30])', {}) == 10
        assert safe_eval('last([10, 20, 30])', {}) == 30

    def test_first_empty(self):
        assert safe_eval('first([])', {}) is None
        assert safe_eval('last([])', {}) is None

    def test_unique(self):
        assert safe_eval('unique([1, 2, 2, 3, 1])', {}) == [1, 2, 3]

    def test_sort_list(self):
        assert safe_eval('sort_list([3, 1, 2])', {}) == [1, 2, 3]
        assert safe_eval('sort_list([3, 1, 2], true)', {}) == [3, 2, 1]

    def test_sort_by(self):
        env = {'users': [{'name': 'Bob', 'age': 30}, {'name': 'Alice', 'age': 25}]}
        result = safe_eval('sort_by(users, "age")', env)
        assert [u['name'] for u in result] == ['Alice', 'Bob']

    def test_flatten(self):
        assert safe_eval('flatten([[1, 2], [3], [4, 5]])', {}) == [1, 2, 3, 4, 5]

    def test_chunk(self):
        assert safe_eval('chunk([1, 2, 3, 4, 5], 2)', {}) == [[1, 2], [3, 4], [5]]

    def test_chunk_invalid(self):
        with pytest.raises(SafeEvalError, match="chunk"):
            safe_eval('chunk([1, 2], 0)', {})

    def test_slice(self):
        assert safe_eval('slice([1, 2, 3, 4], 1, 3)', {}) == [2, 3]
        assert safe_eval('slice([1, 2, 3, 4], 2)', {}) == [3, 4]

    def test_pluck(self):
        env = {'users': [{'name': 'A'}, {'name': 'B'}, {'other': 1}]}
        assert safe_eval('pluck(users, "name")', env) == ['A', 'B', None]

    def test_filter_by_key(self):
        env = {'items': [{'active': True}, {'active': False}, {'active': True}]}
        result = safe_eval('filter_by_key(items, "active", true)', env)
        assert len(result) == 2

    def test_count_where(self):
        env = {'items': [{'status': 'ok'}, {'status': 'err'}, {'status': 'ok'}]}
        assert safe_eval('count_where(items, "status", "ok")', env) == 2


# ---------------------------------------------------------------------------
# DV-04: Object helpers
# ---------------------------------------------------------------------------

class TestObjectHelpers:
    def test_has_key(self):
        env = {'d': {'a': 1, 'b': 2}}
        assert safe_eval('has_key(d, "a")', env) is True
        assert safe_eval('has_key(d, "c")', env) is False

    def test_pick(self):
        env = {'d': {'a': 1, 'b': 2, 'c': 3}}
        assert safe_eval('pick(d, ["a", "c"])', env) == {'a': 1, 'c': 3}

    def test_omit(self):
        env = {'d': {'a': 1, 'b': 2, 'c': 3}}
        assert safe_eval('omit(d, ["b"])', env) == {'a': 1, 'c': 3}

    def test_get_path(self):
        env = {'d': {'a': {'b': {'c': 42}}}}
        assert safe_eval('get_path(d, "a.b.c")', env) == 42
        assert safe_eval('get_path(d, "a.x.y")', env) is None
        assert safe_eval('get_path(d, "a.x.y", "fallback")', env) == "fallback"


# ---------------------------------------------------------------------------
# DV-04: Date / time helpers
# ---------------------------------------------------------------------------

class TestDateHelpers:
    def test_now_is_iso(self):
        result = safe_eval('now()', {})
        assert isinstance(result, str) and "T" in result

    def test_today_is_iso_date(self):
        result = safe_eval('today()', {})
        assert len(result) == 10 and result[4] == "-"

    def test_parse_date_normalises_z_suffix(self):
        out = safe_eval('parse_date("2026-04-21T10:00:00Z")', {})
        assert out.startswith("2026-04-21T10:00:00")
        assert "+00:00" in out

    def test_parse_date_invalid(self):
        with pytest.raises(SafeEvalError, match="parse_date"):
            safe_eval('parse_date("not a date")', {})

    def test_format_date(self):
        assert safe_eval(
            'format_date("2026-04-21T00:00:00Z", "%Y-%m-%d")', {}
        ) == "2026-04-21"

    def test_add_days(self):
        assert safe_eval('add_days("2026-04-21T00:00:00Z", 3)', {}).startswith("2026-04-24")

    def test_add_hours_minutes(self):
        assert safe_eval('add_hours("2026-04-21T00:00:00Z", 5)', {}).startswith("2026-04-21T05:00")
        assert safe_eval('add_minutes("2026-04-21T00:00:00Z", 30)', {}).startswith("2026-04-21T00:30")

    def test_days_between(self):
        assert safe_eval(
            'days_between("2026-04-21T00:00:00Z", "2026-04-25T00:00:00Z")', {}
        ) == 4

    def test_hours_between(self):
        assert safe_eval(
            'hours_between("2026-04-21T00:00:00Z", "2026-04-21T03:30:00Z")', {}
        ) == pytest.approx(3.5)

    def test_is_past_future(self):
        # Relative to now in CI; hardcoded dates either side should be stable.
        assert safe_eval('is_past("2000-01-01T00:00:00Z")', {}) is True
        assert safe_eval('is_future("2099-01-01T00:00:00Z")', {}) is True


# ---------------------------------------------------------------------------
# DV-04: Utility / conversion helpers
# ---------------------------------------------------------------------------

class TestUtilityHelpers:
    def test_is_null(self):
        assert safe_eval('is_null(x)', {'x': None}) is True
        assert safe_eval('is_null(x)', {'x': 0}) is False
        assert safe_eval('is_null(x)', {'x': ""}) is False

    def test_is_empty(self):
        assert safe_eval('is_empty(x)', {'x': None}) is True
        assert safe_eval('is_empty(x)', {'x': ""}) is True
        assert safe_eval('is_empty(x)', {'x': []}) is True
        assert safe_eval('is_empty(x)', {'x': {}}) is True
        assert safe_eval('is_empty(x)', {'x': "a"}) is False
        assert safe_eval('is_empty(x)', {'x': 0}) is False

    def test_default(self):
        assert safe_eval('default(x, "fb")', {'x': None}) == "fb"
        assert safe_eval('default(x, "fb")', {'x': ""}) == "fb"
        assert safe_eval('default(x, "fb")', {'x': "val"}) == "val"

    def test_coalesce(self):
        assert safe_eval('coalesce(a, b, c)', {'a': None, 'b': None, 'c': 42}) == 42
        assert safe_eval('coalesce(a, b)', {'a': None, 'b': None}) is None

    def test_to_json(self):
        assert safe_eval('to_json(x)', {'x': {'a': 1}}) == '{"a":1}'

    def test_parse_json(self):
        assert safe_eval('parse_json(s)', {'s': '{"a":1}'}) == {"a": 1}

    def test_parse_json_invalid(self):
        with pytest.raises(SafeEvalError, match="parse_json"):
            safe_eval('parse_json("not json")', {})

    def test_safe_number(self):
        assert safe_eval('safe_number("42")', {}) == 42
        assert safe_eval('safe_number("3.14")', {}) == 3.14
        assert safe_eval('safe_number("abc", -1)', {}) == -1
        assert safe_eval('safe_number("", 0)', {}) == 0


# ---------------------------------------------------------------------------
# DV-04: Helper combinations — the realistic "condition expression"
# ---------------------------------------------------------------------------

class TestCombinedHelpers:
    def test_filter_and_count(self):
        env = {
            'items': [
                {'status': 'ok', 'n': 1},
                {'status': 'err', 'n': 2},
                {'status': 'ok', 'n': 3},
            ],
        }
        assert safe_eval('count_where(items, "status", "ok") > 1', env) is True

    def test_pluck_sum(self):
        env = {'orders': [{'amount': 10}, {'amount': 20}, {'amount': 5}]}
        assert safe_eval('sum(pluck(orders, "amount"))', env) == 35

    def test_days_between_with_coalesce(self):
        env = {'ts': None}
        assert safe_eval(
            'days_between(coalesce(ts, "2026-04-01T00:00:00Z"), "2026-04-10T00:00:00Z")',
            env,
        ) == 9

    def test_slugify_pluck(self):
        env = {'posts': [{'title': 'Hello World'}, {'title': 'Foo Bar'}]}
        assert safe_eval(
            'slugify(first(pluck(posts, "title")))', env
        ) == "hello-world"
