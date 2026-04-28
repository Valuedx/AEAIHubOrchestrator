import json
from typing import Any

def _sanitize_tool_result(result: Any, max_chars: int = 20000) -> Any:
    """Sanitize and truncate tool output to prevent LLM context bloat."""
    try:
        if isinstance(result, (dict, list)):
            result_str = json.dumps(result, default=str)
            if len(result_str) > max_chars:
                return result_str[:max_chars] + f"\n... [truncated {len(result_str) - max_chars} chars]"
            return result
        
        s = str(result)
        if len(s) > max_chars:
            return s[:max_chars] + f"\n... [truncated {len(s) - max_chars} chars]"
        return result
    except Exception:
        s = str(result)
        if len(s) > max_chars:
            return s[:max_chars] + "... [truncated]"
        return s

data = [{"key": "value" * 10} for _ in range(100)]
print("Original type:", type(data))
print("Original size:", len(json.dumps(data)))

sanitized = _sanitize_tool_result(data, max_chars=100)
print("Sanitized type:", type(sanitized))
print("Sanitized preview:", repr(sanitized))
