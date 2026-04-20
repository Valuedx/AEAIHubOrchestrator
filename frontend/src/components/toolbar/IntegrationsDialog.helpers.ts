/**
 * Extracted helpers for IntegrationsDialog — kept in a separate module so
 * the unit tests can import them without pulling in React / the full
 * dialog tree. Same translation logic the dialog uses internally.
 */

export interface AEConfig {
  baseUrl: string;
  orgCode: string;
  credentialsSecretPrefix: string;
  authMode: "ae_session" | "bearer";
  source: string;
  userId: string;
}

export function emptyAEConfig(): AEConfig {
  return {
    baseUrl: "",
    orgCode: "",
    credentialsSecretPrefix: "AUTOMATIONEDGE",
    authMode: "ae_session",
    source: "AE AI Hub Orchestrator",
    userId: "orchestrator",
  };
}

export function configToRecord(c: AEConfig): Record<string, unknown> {
  return {
    baseUrl: c.baseUrl.trim(),
    orgCode: c.orgCode.trim(),
    credentialsSecretPrefix: c.credentialsSecretPrefix.trim(),
    authMode: c.authMode,
    source: c.source.trim(),
    userId: c.userId.trim(),
  };
}

export function recordToConfig(r: Record<string, unknown> | undefined): AEConfig {
  const base = emptyAEConfig();
  if (!r) return base;
  return {
    baseUrl: typeof r.baseUrl === "string" ? r.baseUrl : base.baseUrl,
    orgCode: typeof r.orgCode === "string" ? r.orgCode : base.orgCode,
    credentialsSecretPrefix:
      typeof r.credentialsSecretPrefix === "string"
        ? r.credentialsSecretPrefix
        : base.credentialsSecretPrefix,
    authMode: r.authMode === "bearer" ? "bearer" : "ae_session",
    source: typeof r.source === "string" ? r.source : base.source,
    userId: typeof r.userId === "string" ? r.userId : base.userId,
  };
}
