/**
 * Unit tests for the IntegrationsDialog config ↔ record helpers.
 *
 * The dialog itself is mostly form state + a couple of API calls;
 * full-render tests are brittle and add little value. We cover the
 * shape-translation helpers (exported below as a side effect of the
 * module) via a direct import + parse round-trip.
 *
 * These tests lock the fact that unknown keys on a server response
 * fall back to safe defaults (empty string or "ae_session") instead
 * of crashing the dialog when the server schema drifts.
 */

import { describe, expect, it } from "vitest";

// Re-import through the module. The helpers are module-local; re-exporting
// them for testability is fine even though the production build doesn't
// use them directly.
import { recordToConfig, configToRecord } from "./IntegrationsDialog.helpers";

describe("AE config ↔ record helpers", () => {
  it("round-trips a full config", () => {
    const original = {
      baseUrl: "http://ae.example.com:8080/aeengine/rest",
      orgCode: "PROD",
      credentialsSecretPrefix: "PROD_AE",
      authMode: "bearer" as const,
      source: "AE AI Hub Orchestrator",
      userId: "orchestrator",
    };
    expect(recordToConfig(configToRecord(original))).toEqual(original);
  });

  it("fills in sensible defaults for missing record fields", () => {
    const cfg = recordToConfig({});
    expect(cfg.authMode).toBe("ae_session");
    expect(cfg.credentialsSecretPrefix).toBe("AUTOMATIONEDGE");
    expect(cfg.source).toBe("AE AI Hub Orchestrator");
    expect(cfg.userId).toBe("orchestrator");
    expect(cfg.baseUrl).toBe("");
    expect(cfg.orgCode).toBe("");
  });

  it("coerces unknown authMode string to ae_session", () => {
    const cfg = recordToConfig({ authMode: "not-a-real-mode" });
    expect(cfg.authMode).toBe("ae_session");
  });

  it("drops non-string values safely", () => {
    const cfg = recordToConfig({ baseUrl: 42, orgCode: null });
    expect(cfg.baseUrl).toBe("");
    expect(cfg.orgCode).toBe("");
  });

  it("configToRecord trims whitespace on string fields", () => {
    const record = configToRecord({
      baseUrl: "  http://x/rest  ",
      orgCode: " DEV ",
      credentialsSecretPrefix: " AE ",
      authMode: "ae_session",
      source: " src ",
      userId: " me ",
    });
    expect(record.baseUrl).toBe("http://x/rest");
    expect(record.orgCode).toBe("DEV");
    expect(record.credentialsSecretPrefix).toBe("AE");
    expect(record.source).toBe("src");
    expect(record.userId).toBe("me");
  });

  it("configToRecord does not lose the authMode literal", () => {
    const record = configToRecord({
      baseUrl: "http://x/rest",
      orgCode: "X",
      credentialsSecretPrefix: "AE",
      authMode: "bearer",
      source: "s",
      userId: "u",
    });
    expect(record.authMode).toBe("bearer");
  });
});
