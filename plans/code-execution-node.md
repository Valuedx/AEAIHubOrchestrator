# Code Execution Node — Implementation Plan

## Goal

Add a sandboxed Python code execution node so users can run custom scripts inside workflows for data transformation, filtering, aggregation, and arbitrary logic — closing the gap with Dify (sandboxed Python/JS), n8n (Code node), and LangGraph (arbitrary Python).

---

## Architecture

### Sandbox Strategy — Subprocess with Restricted Builtins

User code runs in a **separate Python subprocess** with multiple security layers:

| Layer | Mechanism |
|-------|-----------|
| Process isolation | `subprocess.run()` — user code never shares the app process |
| Restricted builtins | Dangerous names removed: `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`, `vars`, `dir`, `breakpoint`, `exit`, `quit`, `input` |
| Import whitelist | Only approved stdlib modules can be imported (see list below) |
| Timeout | Per-node configurable (default 30 s, max from `Settings`) |
| Output size cap | Max 1 MB stdout to prevent memory exhaustion |
| Clean environment | Subprocess inherits only `PATH` and `PYTHONHASHSEED` — no app secrets |
| Empty `sys.path` | Prevents importing anything outside stdlib |

**Whitelisted imports:** `json`, `math`, `re`, `datetime`, `collections`, `itertools`, `functools`, `string`, `textwrap`, `hashlib`, `hmac`, `base64`, `urllib.parse`, `html`, `copy`, `decimal`, `fractions`, `statistics`, `operator`, `typing`, `uuid`, `random`, `time` (with `sleep` monkey-patched out), `enum`, `dataclasses`.

> **Future upgrade path:** When the backend runs inside Docker, a `code_sandbox_mode = "docker"` setting can spin a disposable container per execution for OS-level isolation (network namespace, cgroups). The subprocess approach is the sensible default for non-containerized deployments.

### Data Flow

```
upstream node outputs ──► JSON on stdin ──► user code ──► JSON on stdout ──► downstream context
```

The user's code receives an `inputs` dict (all upstream node outputs keyed by `node_<id>`) and must assign an `output` variable (dict) that becomes the node's result in the workflow context.

### Wrapper Script Template

A temporary `_runner.py` is generated per execution:

```python
import sys, json

# 1. Restrict builtins
_ALLOWED_MODULES = {"json", "math", "re", ...}

_original_import = __builtins__.__import__
def _safe_import(name, *args, **kwargs):
    top = name.split(".")[0]
    if top not in _ALLOWED_MODULES:
        raise ImportError(f"Module '{name}' is not allowed")
    return _original_import(name, *args, **kwargs)

safe_builtins = {k: v for k, v in __builtins__.__dict__.items()
                 if k not in BLOCKED_BUILTINS}
safe_builtins["__import__"] = _safe_import

# 2. Read inputs
inputs = json.loads(sys.stdin.read())

# 3. Execute user code in restricted namespace
namespace = {"__builtins__": safe_builtins, "inputs": inputs}
exec(USER_CODE, namespace)

# 4. Write output
output = namespace.get("output", {})
sys.stdout.write(json.dumps(output, default=str))
```

---

## Node Registry Entry

```json
{
  "type": "code_execution",
  "category": "action",
  "label": "Code",
  "description": "Run sandboxed Python code for data transformation and custom logic",
  "icon": "code",
  "config_schema": {
    "language": {
      "type": "string",
      "default": "python",
      "enum": ["python"],
      "description": "Programming language for the code snippet"
    },
    "code": {
      "type": "string",
      "default": "# 'inputs' contains upstream node outputs.\n# Assign your result to 'output'.\n\noutput = {\"result\": inputs}\n",
      "description": "Python code to execute in a sandboxed subprocess"
    },
    "timeout": {
      "type": "integer",
      "default": 30,
      "min": 1,
      "max": 120,
      "description": "Maximum execution time in seconds"
    }
  }
}
```

---

## Implementation Tasks

### 1. Backend — Sandbox runner (`backend/app/engine/sandbox.py`)

Create a new module with:

- **`SandboxResult`** dataclass: `output: dict`, `stdout: str`, `stderr: str`, `duration_ms: int`, `error: str | None`.
- **`run_python_sandbox(code, inputs, timeout, output_limit)`** function:
  1. Create a temp directory.
  2. Write the wrapper `_runner.py` (embeds user code as a string constant, not via eval).
  3. Serialize `inputs` to JSON.
  4. Execute `subprocess.run([sys.executable, "_runner.py"], input=json_input, capture_output=True, timeout=timeout, cwd=tmpdir, env=clean_env)`.
  5. Parse `stdout` as JSON → `output`.
  6. On `TimeoutExpired`, kill and return error.
  7. On non-zero exit, capture `stderr` as error.
  8. Clean up temp dir.
  9. Enforce `output_limit` on stdout length.
  10. Return `SandboxResult`.

### 2. Backend — Node handler addition (`backend/app/engine/node_handlers.py`)

- Add `_handle_code_execution(node_data, context, tenant_id)`.
  - Reads `code`, `language`, `timeout` from `node_data["config"]`.
  - Validates `code_sandbox_enabled` from settings; returns error if disabled.
  - Caps `timeout` at `settings.code_sandbox_timeout_max`.
  - Builds `inputs` from `context` (upstream node outputs).
  - Calls `run_python_sandbox(...)`.
  - Returns `result.output` or `{"error": result.error, "stderr": result.stderr}`.
- Add dispatch branch in `dispatch_node` for `label == "Code"`.

### 3. Backend — Configuration (`backend/app/config.py`)

Add to `Settings`:

```python
code_sandbox_enabled: bool = True
code_sandbox_timeout_max: int = 120
code_sandbox_output_limit_bytes: int = 1_048_576  # 1 MB
```

### 4. Shared — Node registry (`shared/node_registry.json`)

Add the `code_execution` entry shown above to `node_types` array.

### 5. Frontend — Icon mapping (`frontend/src/components/nodes/AgenticNode.tsx`)

Import Lucide `Code2` and add `"code": Code2` to `ICON_MAP`.

### 6. Frontend — Config form (`frontend/src/components/sidebar/DynamicConfigForm.tsx`)

Add a special case: when node `type` is `code_execution` and field key is `code`, render a **monospace `<textarea>`** with:
- Min height ~200 px, resizable.
- `font-family: monospace`, `tab-size: 2`.
- Placeholder text matching the default code.
- `onKeyDown` handler that inserts literal tab characters (prevents focus loss).

The `language` and `timeout` fields use existing enum select / number input rendering — no changes needed for those.

### 7. Frontend — Type update (`frontend/src/types/nodes.ts`)

No changes needed — `code_execution` falls under the existing `"action"` category.

### 8. Codewiki updates

- **`codewiki/node-types.md`** — add Code Execution node documentation.
- **`codewiki/feature-roadmap.md`** — mark feature #2 as **Done**.

---

## Security considerations

| Risk | Mitigation |
|------|------------|
| Arbitrary file access | `open` removed from builtins; `os`, `pathlib`, `shutil` not in import whitelist |
| Network exfiltration | `socket`, `http`, `urllib.request`, `requests` not in import whitelist |
| Resource exhaustion (CPU) | Subprocess timeout kills process |
| Resource exhaustion (memory) | Output cap at 1 MB; future: `resource.setrlimit` on Linux |
| Import smuggling | Custom `__import__` checks top-level module against whitelist |
| Code injection in wrapper | User code embedded as raw string constant, never interpolated into Python syntax |
| Secrets leakage | Subprocess env stripped of all app env vars except `PATH` |

---

## Out of scope (future enhancements)

- JavaScript execution (via Node.js subprocess + `vm` module) — add `"javascript"` to `language` enum later.
- Docker-based sandbox for full OS-level isolation.
- Per-tenant code execution quotas.
- Shared code libraries / snippets.
- Inline code editor with syntax highlighting (e.g., Monaco) — current plan uses a styled textarea.
