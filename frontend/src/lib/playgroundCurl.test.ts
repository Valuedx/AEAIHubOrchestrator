import { describe, expect, it } from "vitest";
import { buildCurl } from "./playgroundCurl";


describe("buildCurl", () => {
  it("emits a complete sync curl with the default tenant", () => {
    const out = buildCurl({
      workflowId: "wf-123",
      payload: { message: "hi" },
      sync: true,
      syncTimeout: 60,
      baseUrl: "http://localhost:8001",
      tenantId: "default",
    });

    expect(out).toContain("curl -X POST 'http://localhost:8001/api/v1/workflows/wf-123/execute'");
    expect(out).toContain("-H 'Content-Type: application/json'");
    expect(out).toContain("-H 'X-Tenant-Id: default'");
    expect(out).toContain('"sync":true');
    expect(out).toContain('"sync_timeout":60');
    expect(out).toContain('"trigger_payload":{"message":"hi"}');
  });

  it("uses async shape when sync=false (no sync_timeout)", () => {
    const out = buildCurl({
      workflowId: "wf-a",
      payload: {},
      sync: false,
      baseUrl: "http://orch",
      tenantId: "t",
    });
    expect(out).toContain('"sync":false');
    expect(out).not.toContain("sync_timeout");
  });

  it("defaults sync_timeout to 120 when sync=true and none is passed", () => {
    const out = buildCurl({
      workflowId: "wf-b",
      payload: null,
      sync: true,
      baseUrl: "http://orch",
      tenantId: "t",
    });
    expect(out).toContain('"sync_timeout":120');
  });

  it("emits a Bearer placeholder in oidc mode", () => {
    const out = buildCurl({
      workflowId: "wf-c",
      payload: {},
      sync: true,
      baseUrl: "http://orch",
      authMode: "oidc",
    });
    expect(out).toContain("Authorization: Bearer <your-oidc-access-token>");
    expect(out).not.toContain("X-Tenant-Id");
  });

  it("falls back to localhost:8001 when baseUrl is empty", () => {
    const out = buildCurl({
      workflowId: "wf-d",
      payload: {},
      sync: false,
      baseUrl: "",
      tenantId: "t",
    });
    expect(out).toContain("http://localhost:8001/api/v1/workflows/wf-d/execute");
  });

  it("strips trailing slashes from baseUrl so the url has exactly one", () => {
    const out = buildCurl({
      workflowId: "wf-e",
      payload: {},
      sync: false,
      baseUrl: "http://orch/",
      tenantId: "t",
    });
    expect(out).toContain("http://orch/api/v1/workflows/wf-e/execute");
  });

  it("escapes single quotes in the payload body so bash parses the string", () => {
    // If the payload contains a single quote, the emitted string must
    // break out of the bash single-quote run so the shell sees the
    // embedded quote literally.
    const out = buildCurl({
      workflowId: "wf-f",
      payload: { q: "don't break" },
      sync: false,
      baseUrl: "http://orch",
      tenantId: "t",
    });
    // The bash-safe escape turns ' into '\''  (close, escape, reopen).
    expect(out).toContain("'\\''");
  });

  it("serialises a null payload as `trigger_payload: null`", () => {
    const out = buildCurl({
      workflowId: "wf-g",
      payload: null,
      sync: false,
      baseUrl: "http://orch",
      tenantId: "t",
    });
    expect(out).toContain('"trigger_payload":null');
  });

  it("emits deterministic_mode: true only when set", () => {
    const on = buildCurl({
      workflowId: "wf-h",
      payload: {},
      sync: false,
      deterministicMode: true,
      baseUrl: "http://orch",
      tenantId: "t",
    });
    const off = buildCurl({
      workflowId: "wf-h",
      payload: {},
      sync: false,
      baseUrl: "http://orch",
      tenantId: "t",
    });
    expect(on).toContain('"deterministic_mode":true');
    expect(off).toContain('"deterministic_mode":false');
  });
});
