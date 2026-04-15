---
name: Fix Critical System Errors and Security Risks
overview: Comprehensive fix for critical runtime errors, security vulnerabilities, and logic flaws in the agent, executor, and metadata systems.
todos:
  - id: fix-runtime-errors
    content: Fix LlmRequest and ToolResult runtime errors in agent.py and executor.py
    status: pending
  - id: fix-security-risks
    content: Fix PII redaction and metadata leak in guardrails.py and list_accessible_tables.py
    status: pending
  - id: fix-persistence-logic
    content: Ensure conversation persistence during clarification flows in agent.py
    status: pending
  - id: fix-metadata-logic
    content: Restore database descriptions and fix entity map collisions in metadata system
    status: pending
  - id: refine-sql-and-prompts
    content: Refine SQL safety regex and update planner prompt templates
    status: pending
  - id: add-missing-tests
    content: Add unit tests for plan synthesis, SQL repair, and redaction
    status: pending
---

