"""Sandboxed Python code execution via subprocess.

User code runs in a separate Python process with restricted builtins,
a module import whitelist, and configurable timeout / output limits.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import TimeoutExpired, run
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "json",
        "math",
        "re",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "string",
        "textwrap",
        "hashlib",
        "hmac",
        "base64",
        "urllib.parse",
        "html",
        "copy",
        "decimal",
        "fractions",
        "statistics",
        "operator",
        "typing",
        "uuid",
        "random",
        "enum",
        "dataclasses",
        "time",
    }
)

BLOCKED_BUILTINS: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "breakpoint",
        "exit",
        "quit",
        "input",
        "memoryview",
        "help",
    }
)


@dataclass
class SandboxResult:
    output: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: str | None = None


_RUNNER_TEMPLATE = textwrap.dedent("""\
    import sys as _sys
    import json as _json

    # ── Restrict builtins ──
    _BLOCKED = {blocked_builtins!r}

    _original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
    _ALLOWED = {allowed_modules!r}

    def _safe_import(name, *args, **kwargs):
        top = name.split(".")[0]
        if top not in _ALLOWED:
            raise ImportError(f"Import of '{{name}}' is not allowed in sandbox")
        return _original_import(name, *args, **kwargs)

    _safe_builtins = {{}}
    _src = __builtins__.__dict__ if hasattr(__builtins__, '__dict__') else __builtins__
    if isinstance(_src, dict):
        _safe_builtins = {{k: v for k, v in _src.items() if k not in _BLOCKED}}
    else:
        _safe_builtins = {{k: getattr(_src, k) for k in dir(_src) if k not in _BLOCKED}}
    _safe_builtins["__import__"] = _safe_import

    # Neuter time.sleep to prevent stalling
    import time as _time_mod
    _time_mod.sleep = lambda *a, **kw: None

    # Clear sys.path so local modules cannot be imported
    _sys.path = []

    # ── Read inputs ──
    _input_json = _sys.stdin.read()
    inputs = _json.loads(_input_json) if _input_json else {{}}

    # ── Execute user code ──
    _namespace = {{"__builtins__": _safe_builtins, "inputs": inputs}}

    try:
        exec({user_code!r}, _namespace)
    except Exception as _exc:
        _sys.stderr.write(str(_exc))
        _sys.exit(1)

    # ── Write output ──
    _output = _namespace.get("output", {{}})
    if not isinstance(_output, dict):
        _output = {{"result": _output}}

    _out_str = _json.dumps(_output, default=str)
    # Enforce output size limit
    _limit = {output_limit}
    if len(_out_str) > _limit:
        _sys.stderr.write(f"Output exceeds {{_limit}} byte limit (got {{len(_out_str)}})")
        _sys.exit(1)

    _sys.stdout.write(_out_str)
""")


def run_python_sandbox(
    code: str,
    inputs: dict[str, Any],
    timeout: int = 30,
    output_limit: int = 1_048_576,
) -> SandboxResult:
    """Execute *code* in a sandboxed subprocess and return the result."""
    start = time.monotonic()

    tmpdir = tempfile.mkdtemp(prefix="orch_sandbox_")
    runner_path = Path(tmpdir) / "_runner.py"

    try:
        runner_source = _RUNNER_TEMPLATE.format(
            blocked_builtins=set(BLOCKED_BUILTINS),
            allowed_modules=set(ALLOWED_MODULES),
            user_code=code,
            output_limit=output_limit,
        )
        runner_path.write_text(runner_source, encoding="utf-8")

        input_json = json.dumps(inputs, default=str)

        clean_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "random",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if sys.platform == "win32":
            clean_env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")

        proc = run(
            [sys.executable, str(runner_path)],
            input=input_json,
            capture_output=True,
            timeout=timeout,
            cwd=tmpdir,
            env=clean_env,
            text=True,
        )

        duration_ms = int((time.monotonic() - start) * 1000)

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            logger.warning(
                "Sandbox exited with code %d after %d ms: %s",
                proc.returncode, duration_ms, stderr[:500],
            )
            return SandboxResult(
                stdout=proc.stdout or "",
                stderr=stderr,
                duration_ms=duration_ms,
                error=stderr or f"Process exited with code {proc.returncode}",
            )

        stdout = proc.stdout or ""
        if len(stdout) > output_limit:
            return SandboxResult(
                stdout="",
                stderr="Output exceeded size limit",
                duration_ms=duration_ms,
                error=f"Output exceeded {output_limit} byte limit",
            )

        try:
            output = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            return SandboxResult(
                stdout=stdout[:2000],
                stderr=proc.stderr or "",
                duration_ms=duration_ms,
                error=f"Failed to parse output as JSON: {exc}",
            )

        return SandboxResult(
            output=output,
            stdout=stdout[:2000],
            stderr=(proc.stderr or "").strip(),
            duration_ms=duration_ms,
        )

    except TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("Sandbox timed out after %d ms (limit=%d s)", duration_ms, timeout)
        return SandboxResult(
            duration_ms=duration_ms,
            error=f"Execution timed out after {timeout} seconds",
        )

    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("Sandbox runner error: %s", exc)
        return SandboxResult(
            duration_ms=duration_ms,
            error=f"Sandbox error: {exc}",
        )

    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
