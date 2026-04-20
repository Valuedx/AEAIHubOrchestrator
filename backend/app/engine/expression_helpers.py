"""DV-04 — Expression helpers library for safe_eval.

These helpers live in their own module so they can be unit-tested
independently of the AST walker and, critically, kept in one place when
we extend them. The safe_eval module imports ``EXPRESSION_HELPERS``
and merges it into its ``_WHITELISTED_FUNCTIONS`` dict.

Guiding principles:

* Pure, deterministic, no I/O, no env access. These run inside the
  same process as the DAG runner and must be fast + side-effect-free.
* Lenient on bad input where it's obvious (``default(None, "x")`` →
  ``"x"``), strict where a silent wrong answer would be confusing
  (e.g. ``days_between`` raises on unparseable strings).
* Size-capped wherever the output scales with an arg (``repeat``,
  ``chunk``) so a bad expression can't OOM the worker.
* Names short and template-friendly — these show up in
  ``{{ ... }}`` expressions on a busy canvas.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable

# Hard caps — tune here, not in call sites.
_MAX_REPEAT_N = 10_000
_MAX_REPEAT_LEN = 1_000_000  # 1 MB
_MAX_JSON_PARSE_LEN = 1_000_000  # 1 MB


class ExpressionHelperError(ValueError):
    """Raised for obviously-wrong inputs (bad date, negative repeat, ...)."""


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------


def _trim(s: Any) -> str:
    return str(s).strip()


def _replace(s: Any, old: Any, new: Any) -> str:
    return str(s).replace(str(old), str(new))


def _split(s: Any, sep: Any) -> list[str]:
    return str(s).split(str(sep))


def _join(sep: Any, arr: Iterable[Any]) -> str:
    return str(sep).join(str(x) for x in arr)


def _substring(s: Any, start: int, end: int | None = None) -> str:
    text = str(s)
    if end is None:
        return text[int(start):]
    return text[int(start):int(end)]


def _left(s: Any, n: int) -> str:
    n = int(n)
    if n <= 0:
        return ""
    return str(s)[:n]


def _right(s: Any, n: int) -> str:
    n = int(n)
    if n <= 0:
        return ""
    return str(s)[-n:]


def _pad_left(s: Any, width: int, char: str = " ") -> str:
    ch = str(char)[:1] or " "
    return str(s).rjust(int(width), ch)


def _pad_right(s: Any, width: int, char: str = " ") -> str:
    ch = str(char)[:1] or " "
    return str(s).ljust(int(width), ch)


def _repeat(s: Any, n: int) -> str:
    n = int(n)
    if n < 0:
        raise ExpressionHelperError("repeat(n) requires n >= 0")
    if n > _MAX_REPEAT_N:
        raise ExpressionHelperError(f"repeat(n) capped at {_MAX_REPEAT_N}")
    text = str(s)
    if len(text) * n > _MAX_REPEAT_LEN:
        raise ExpressionHelperError(
            f"repeat result would exceed {_MAX_REPEAT_LEN} chars"
        )
    return text * n


def _reverse(x: Any) -> Any:
    if isinstance(x, str):
        return x[::-1]
    if isinstance(x, list):
        return list(reversed(x))
    raise ExpressionHelperError(f"reverse() not supported on {type(x).__name__}")


def _truncate(s: Any, n: int, suffix: str = "…") -> str:
    text = str(s)
    n = int(n)
    if n <= 0:
        return ""
    if len(text) <= n:
        return text
    suf = str(suffix)
    # Preserve the cap — the full result is never longer than ``n``.
    if n <= len(suf):
        return text[:n]
    return text[: n - len(suf)] + suf


def _title_case(s: Any) -> str:
    return str(s).title()


def _snake_case(s: Any) -> str:
    text = str(s)
    # Insert _ between camelCase / PascalCase boundaries.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    # Collapse whitespace / punctuation runs into a single _.
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_").lower()


def _camel_case(s: Any) -> str:
    # Start from snake_case so we get a clean tokenisation.
    snake = _snake_case(s)
    parts = [p for p in snake.split("_") if p]
    if not parts:
        return ""
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _regex_replace(s: Any, pattern: str, repl: str) -> str:
    try:
        return re.sub(str(pattern), str(repl), str(s))
    except re.error as exc:
        raise ExpressionHelperError(f"Invalid regex: {exc}") from exc


def _regex_extract(s: Any, pattern: str) -> str | None:
    try:
        m = re.search(str(pattern), str(s))
    except re.error as exc:
        raise ExpressionHelperError(f"Invalid regex: {exc}") from exc
    if m is None:
        return None
    # Prefer the first capture group so callers can steer extraction
    # with parentheses; fall back to the full match.
    if m.groups():
        return m.group(1)
    return m.group(0)


def _slugify(s: Any) -> str:
    text = unicodedata.normalize("NFKD", str(s))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "-", text)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _round(x: Any, digits: int = 0) -> float | int:
    return round(float(x), int(digits))


def _ceil(x: Any) -> int:
    return math.ceil(float(x))


def _floor(x: Any) -> int:
    return math.floor(float(x))


def _sum(arr: Iterable[Any]) -> float | int:
    total: float = 0
    for v in arr:
        total += float(v)
    # Preserve int-ness when every value was integral.
    return int(total) if total == int(total) else total


def _avg(arr: Iterable[Any]) -> float:
    values = [float(v) for v in arr]
    if not values:
        raise ExpressionHelperError("avg() on empty sequence")
    return sum(values) / len(values)


def _median(arr: Iterable[Any]) -> float:
    values = [float(v) for v in arr]
    if not values:
        raise ExpressionHelperError("median() on empty sequence")
    return float(statistics.median(values))


def _clamp(x: Any, lo: Any, hi: Any) -> float | int:
    xv, lov, hiv = float(x), float(lo), float(hi)
    if lov > hiv:
        raise ExpressionHelperError("clamp(lo, hi) requires lo <= hi")
    return max(lov, min(hiv, xv))


# ---------------------------------------------------------------------------
# Array helpers
# ---------------------------------------------------------------------------


def _first(arr: Any) -> Any:
    if not isinstance(arr, (list, tuple, str)) or len(arr) == 0:
        return None
    return arr[0]


def _last(arr: Any) -> Any:
    if not isinstance(arr, (list, tuple, str)) or len(arr) == 0:
        return None
    return arr[-1]


def _unique(arr: Iterable[Any]) -> list[Any]:
    seen: list[Any] = []
    for v in arr:
        if v not in seen:
            seen.append(v)
    return seen


def _sort_list(arr: Iterable[Any], reverse: bool = False) -> list[Any]:
    return sorted(list(arr), key=_sort_key, reverse=bool(reverse))


def _sort_by(arr: Iterable[Any], key: str, reverse: bool = False) -> list[Any]:
    k = str(key)
    return sorted(
        list(arr),
        key=lambda item: _sort_key(item.get(k) if isinstance(item, dict) else None),
        reverse=bool(reverse),
    )


def _sort_key(v: Any) -> tuple:
    # Stable-order helper: ``None`` sorts before everything; within
    # the non-None bucket we rely on natural ordering but coerce
    # numbers to float so int/float don't compare funny.
    if v is None:
        return (0, 0)
    if isinstance(v, bool):
        return (1, int(v))
    if isinstance(v, (int, float)):
        return (1, float(v))
    return (2, str(v))


def _flatten(arr: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for v in arr:
        if isinstance(v, list):
            out.extend(v)
        else:
            out.append(v)
    return out


def _chunk(arr: Iterable[Any], n: int) -> list[list[Any]]:
    n = int(n)
    if n <= 0:
        raise ExpressionHelperError("chunk(n) requires n >= 1")
    items = list(arr)
    return [items[i : i + n] for i in range(0, len(items), n)]


def _slice_list(arr: Any, start: int, end: int | None = None) -> list[Any]:
    if not isinstance(arr, (list, tuple, str)):
        raise ExpressionHelperError(
            f"slice() requires list/tuple/str, got {type(arr).__name__}"
        )
    if end is None:
        return list(arr[int(start):])
    return list(arr[int(start):int(end)])


def _pluck(arr: Iterable[Any], key: str) -> list[Any]:
    k = str(key)
    return [item.get(k) for item in arr if isinstance(item, dict)]


def _filter_by_key(arr: Iterable[Any], key: str, value: Any) -> list[Any]:
    k = str(key)
    return [item for item in arr if isinstance(item, dict) and item.get(k) == value]


def _count_where(arr: Iterable[Any], key: str, value: Any) -> int:
    return len(_filter_by_key(arr, key, value))


# ---------------------------------------------------------------------------
# Object helpers
# ---------------------------------------------------------------------------


def _has_key(d: Any, key: str) -> bool:
    return isinstance(d, dict) and str(key) in d


def _pick(d: Any, keys: Iterable[Any]) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    wanted = [str(k) for k in keys]
    return {k: d[k] for k in wanted if k in d}


def _omit(d: Any, keys: Iterable[Any]) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    drop = {str(k) for k in keys}
    return {k: v for k, v in d.items() if k not in drop}


def _get_path(d: Any, path: str, default: Any = None) -> Any:
    """Walk a dotted path across nested dicts. ``a.b.c`` → ``d["a"]["b"]["c"]``."""
    cur: Any = d
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


# ---------------------------------------------------------------------------
# Date / time helpers
# ---------------------------------------------------------------------------

# Clock indirection so tests can freeze time via monkeypatch.
def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        raise ExpressionHelperError("parse_date() got empty string")
    # ``Z`` suffix is common in JSON timestamps — fromisoformat
    # supports it on 3.11+ but we normalise explicitly for safety.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ExpressionHelperError(f"parse_date() invalid ISO datetime: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now() -> str:
    return _utcnow().isoformat()


def _today() -> str:
    return _utcnow().date().isoformat()


def _parse_date(s: Any) -> str:
    # Round-trip through the normaliser so "2026-04-21T00:00:00Z" and
    # "2026-04-21 00:00:00+00:00" return the same canonical ISO string.
    return _parse_datetime(s).isoformat()


def _format_date(value: Any, fmt: str) -> str:
    return _parse_datetime(value).strftime(str(fmt))


def _add_days(value: Any, days: int) -> str:
    return (_parse_datetime(value) + timedelta(days=int(days))).isoformat()


def _add_hours(value: Any, hours: int) -> str:
    return (_parse_datetime(value) + timedelta(hours=int(hours))).isoformat()


def _add_minutes(value: Any, minutes: int) -> str:
    return (_parse_datetime(value) + timedelta(minutes=int(minutes))).isoformat()


def _days_between(a: Any, b: Any) -> int:
    delta = _parse_datetime(b) - _parse_datetime(a)
    return int(delta.total_seconds() // 86400)


def _hours_between(a: Any, b: Any) -> float:
    delta = _parse_datetime(b) - _parse_datetime(a)
    return delta.total_seconds() / 3600


def _is_past(value: Any) -> bool:
    return _parse_datetime(value) < _utcnow()


def _is_future(value: Any) -> bool:
    return _parse_datetime(value) > _utcnow()


# ---------------------------------------------------------------------------
# Conversion / utility helpers
# ---------------------------------------------------------------------------


def _is_null(x: Any) -> bool:
    return x is None


def _is_empty(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, (str, list, tuple, dict, set)):
        return len(x) == 0
    return False


def _default_value(x: Any, fallback: Any) -> Any:
    """Return ``fallback`` when ``x`` is null OR empty — the combined
    check is what operators actually want 95% of the time."""
    return fallback if _is_empty(x) else x


def _coalesce(*args: Any) -> Any:
    for v in args:
        if v is not None:
            return v
    return None


def _to_json(x: Any) -> str:
    return json.dumps(x, default=str, separators=(",", ":"))


def _parse_json(s: Any) -> Any:
    text = str(s)
    if len(text) > _MAX_JSON_PARSE_LEN:
        raise ExpressionHelperError(
            f"parse_json input exceeds {_MAX_JSON_PARSE_LEN} chars"
        )
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ExpressionHelperError(f"parse_json() invalid JSON: {exc}") from exc


def _safe_number(x: Any, fallback: float | int | None = 0) -> Any:
    try:
        if isinstance(x, bool):
            return int(x)
        if isinstance(x, (int, float)):
            return x
        s = str(x).strip()
        if not s:
            return fallback
        if "." in s or "e" in s or "E" in s:
            return float(s)
        return int(s)
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


EXPRESSION_HELPERS: dict[str, Callable[..., Any]] = {
    # String
    "trim": _trim,
    "replace": _replace,
    "split": _split,
    "join": _join,
    "substring": _substring,
    "left": _left,
    "right": _right,
    "pad_left": _pad_left,
    "pad_right": _pad_right,
    "repeat": _repeat,
    "reverse": _reverse,
    "truncate": _truncate,
    "title_case": _title_case,
    "snake_case": _snake_case,
    "camel_case": _camel_case,
    "regex_replace": _regex_replace,
    "regex_extract": _regex_extract,
    "slugify": _slugify,
    # Math
    "round": _round,
    "ceil": _ceil,
    "floor": _floor,
    "sum": _sum,
    "avg": _avg,
    "median": _median,
    "clamp": _clamp,
    # Array
    "first": _first,
    "last": _last,
    "unique": _unique,
    "sort_list": _sort_list,
    "sort_by": _sort_by,
    "flatten": _flatten,
    "chunk": _chunk,
    "slice": _slice_list,
    "pluck": _pluck,
    "filter_by_key": _filter_by_key,
    "count_where": _count_where,
    # Object
    "has_key": _has_key,
    "pick": _pick,
    "omit": _omit,
    "get_path": _get_path,
    # Date / time
    "now": _now,
    "today": _today,
    "parse_date": _parse_date,
    "format_date": _format_date,
    "add_days": _add_days,
    "add_hours": _add_hours,
    "add_minutes": _add_minutes,
    "days_between": _days_between,
    "hours_between": _hours_between,
    "is_past": _is_past,
    "is_future": _is_future,
    # Conversion / utility
    "is_null": _is_null,
    "is_empty": _is_empty,
    "default": _default_value,
    "coalesce": _coalesce,
    "to_json": _to_json,
    "parse_json": _parse_json,
    "safe_number": _safe_number,
}
