"""Minimal execute + poll example for external callers.

Usage:
    python python_client.py <workflow-id> --message "Hello"
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def _request(url: str, *, method: str = "GET", payload: dict | None = None, headers: dict | None = None) -> dict:
    data = None
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow_id")
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--message", default="Hello from the portable orchestrator client")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    headers = {"X-Tenant-Id": args.tenant_id}
    execute_url = f"{args.base_url.rstrip('/')}/api/v1/workflows/{args.workflow_id}/execute"

    try:
        instance = _request(
            execute_url,
            method="POST",
            payload={"trigger_payload": {"message": args.message}},
            headers=headers,
        )
    except urllib.error.HTTPError as exc:
        print(f"Execute failed: HTTP {exc.code} {exc.reason}")
        return 1

    instance_id = instance["id"]
    context_url = (
        f"{args.base_url.rstrip('/')}/api/v1/workflows/"
        f"{args.workflow_id}/instances/{instance_id}/context?x_tenant_id={args.tenant_id}"
    )

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        ctx = _request(context_url, headers=headers)
        status = ctx.get("status")
        print(f"instance={instance_id} status={status}")
        if status in {"completed", "failed", "cancelled", "paused", "suspended"}:
            print(json.dumps(ctx, indent=2))
            return 0 if status == "completed" else 2
        time.sleep(2)

    print(f"Timed out waiting for workflow {args.workflow_id} instance {instance_id}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
