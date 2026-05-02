# Eval mock infrastructure

How to test agent behavior against controlled tool-response failure
modes (agent stopped, dependency down, validation 422, persistent
4xx, transient 5xx, timeout, empty error) **without** staging those
failure modes on the live AE backend.

## Architecture

Three processes already running for the demo + eval:

```
┌─────────────────────┐  POST trigger    ┌──────────────────┐  MCP tool call  ┌──────────────────┐
│  Eval harness       │ ──────────────►  │ Orchestrator      │ ──────────────► │ MCP server       │
│  (run_ae_ops_evals) │                  │ (port 8001)       │                 │ (port 3000)      │
└──────────┬──────────┘                  └──────────────────┘                 └────────┬─────────┘
           │ POST /api/eval/fixture                                                     │
           │ DELETE /api/eval/fixture                                                   │ reads
           ▼                                                                            │
┌─────────────────────┐    write fixture file    ┌──────────────────────────────────────┴───┐
│ Flask test server   │ ──────────────────────►  │ mcp_server/state/eval_fixtures_active.json│
│ (port 5050)         │                          │  (single shared file, mtime-watched)      │
└─────────────────────┘                          └──────────────────────────────────────────┘
```

The eval harness POSTs a per-case fixture to the Flask server before
each case. The MCP server's `eval_mock.py` watches that fixture file
on every tool call (cheap mtime check); when it changes, fixtures
reload. Tool calls matching a fixture entry get the canned response;
tool calls without a fixture pass through to the real AE backend.

When no fixture is loaded (default / production), zero behavior
change — the wrapper passes every call through unchanged.

## Fixture format

```json
{
  "case_id": "claim-status-agent-stopped",
  "fixtures": {
    "ae_agent_get_status": [
      {
        "match": {"agent_id": "MG48"},
        "responses": [
          {"agent_id": "MG48", "state": "STOPPED", "last_seen": "2026-05-01T22:13:00Z"}
        ]
      }
    ],
    "case_add_evidence": [
      {
        "match": {},
        "responses": [
          {"ok": false, "status_code": 422, "error": "case_id missing"},
          {"ok": true, "evidence_id": "ev-7"}
        ]
      }
    ]
  }
}
```

### Tool name format

Use **underscore form** — `ae.agent.get_status` becomes
`ae_agent_get_status`. The MCP server normalizes dots to underscores
when registering tools; the wrapper key uses the underscore form.

### `match`

* `{"agent_id": "MG48"}` — all keys must equal in the call args.
  Extra keys in the call are fine.
* `{}` (empty) — match any call args. Useful when the test doesn't
  care about specific values.
* Per-key wildcard: a value of `"*"` matches any value (including
  missing).

### `responses`

A list of responses returned in order on consecutive calls. After
the list is exhausted, the last response is returned indefinitely
(clamped). This is how a single fixture entry can simulate
"first call fails, second call succeeds" or "fails twice, then
recovers".

## Per-case fixtures in eval transcripts

Add `mock_fixtures` to a case in `ae_ops_eval_transcripts.json`:

```json
{
  "id": "tool-validation-error-recovers",
  "tier": "extended",
  "user_role": "tech",
  "turns": [...],
  "mock_fixtures": {
    "case_add_evidence": [
      {
        "match": {},
        "responses": [
          {"ok": false, "status_code": 422, "error": "case_id missing"},
          {"ok": true, "evidence_id": "ev-7"}
        ]
      }
    ]
  }
}
```

The eval harness installs this fixture before the case runs and
clears it after. Sequential cases get sequential fixtures.

## Running

Both processes need to be running, with the MCP server having the
`eval_mock.py` wrapper hooked into tool registration (already done
in `mcp_server/server.py` — restart the MCP server to pick up the
change once after deploying).

```bash
# Restart MCP server (port 3000) to load the eval_mock wrapper:
# (kill old process, restart with whatever cmdline you usually use)
python -m mcp_server --transport streamable-http --port 3000

# Restart Flask test server (port 5050) to expose /api/eval/fixture:
python agent_server.py

# Run eval — for cases with mock_fixtures, the harness POSTs the
# fixture before each run and clears it after.
cd backend
python -m scratch.run_ae_ops_evals \
  --workflow d4950026-46a7-4c08-91be-1ba08b3dae48:V10 \
  --tier core \
  --out v10_eval.md
```

If the Flask server is unreachable when a mocked case runs, the
harness marks the case as `skipped` (not failed) — distinguishing
infrastructure unavailability from behavior bugs.

## Debugging

Inspect the active fixture:

```bash
curl http://localhost:5050/api/eval/fixture
```

Manually install / clear:

```bash
curl -X POST http://localhost:5050/api/eval/fixture \
     -H "Content-Type: application/json" \
     -d @some-fixture.json

curl -X DELETE http://localhost:5050/api/eval/fixture
```

The MCP server logs each mock hit at INFO:

```
INFO ae_mcp.eval_mock: HIT case='claim-status-agent-stopped'
       tool='ae_agent_get_status' idx=0/0 match={'agent_id': 'MG48'}
```

## When to mock vs use real AE

| Use real AE | Use mock |
|---|---|
| Tests "agent calls the right tool given X" | Tests "agent recovers when tool returns 422" |
| Tests "agent doesn't fabricate when answer is unknown" | Tests "agent escalates after 2nd 400" |
| Capability checks against actual T4 inventory | Specific failure modes that can't be staged on T4 |
| Demo against live data | Regression coverage of error-handling code paths |

Both are valuable. The eval suite mixes them: ~30 cases use real T4,
~5 use mocks (counts as of 2026-05-02).

## Adding a new mock case

1. In `ae_ops_eval_transcripts.json`, add a case with `tier: "extended"`,
   `themes: ["error_handling"]`, and a `mock_fixtures` block.
2. The fixture `match` should be `{}` if you don't care about specific
   args, or `{"<arg>": "<value>"}` for selective matching.
3. The fixture `responses` should mimic the **real** tool's response
   shape — look at a real call result in an instance log first
   (`/api/v1/workflows/<wf>/instances/<id>` → `logs[].output_json
   .iterations_full[].tool_results`).
4. For sequential behavior (fail-then-succeed, succeed-then-fail),
   list multiple responses; the engine returns them in order.
5. Run the case alone first (`--case <id>`) to verify the fixture
   shape matches what the agent expects.
