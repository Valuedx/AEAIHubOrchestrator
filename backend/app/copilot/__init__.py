"""COPILOT-01 — workflow authoring copilot backend.

Vertical slice COPILOT-01a ships the draft-workspace safety boundary
plus the pure tool-layer functions. The agent runner (which actually
drives an LLM through this tool surface) and the system-KB RAG
ingestion land in COPILOT-01b.

Module layout:

  * ``tool_layer``   — pure graph-mutation helpers. Take a graph dict,
    return a new graph dict. No DB, no tenant_id. The HTTP layer and
    the agent runner both call these.
"""
