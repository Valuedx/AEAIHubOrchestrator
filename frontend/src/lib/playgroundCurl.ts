/**
 * API-18A — generate the curl command equivalent to a Playground run.
 *
 * Pure so the dialog can show a "Copy as curl" button without any DOM /
 * clipboard concern — the caller grabs the string from here and writes
 * it to the clipboard. Keep the output compatible with bash's
 * single-quote rules (no shell escape of internal chars other than
 * single-quotes themselves) so operators can paste it straight into a
 * terminal.
 */

export interface CurlOptions {
  /** Workflow UUID. */
  workflowId: string;
  /** Parsed trigger payload. Omit or pass ``null`` to send no body. */
  payload: unknown;
  /** Sync-mode flag (matches ``ExecuteRequest.sync``). */
  sync: boolean;
  /** Sync timeout in seconds. Emitted only when non-default AND sync=true. */
  syncTimeout?: number;
  /** Deterministic mode. Emitted only when true. */
  deterministicMode?: boolean;
  /** Backend base URL. Falls back to ``VITE_API_URL`` or localhost. */
  baseUrl?: string;
  /** Tenant id. Falls back to ``VITE_TENANT_ID`` or ``"default"``. */
  tenantId?: string;
  /** ``oidc`` → emit a placeholder Bearer header instead of the tenant header. */
  authMode?: string;
}

/** Escape a string for bash single-quoting.
 *
 *  Inside single quotes bash is literal, so the only character we need
 *  to handle is a single quote itself — break the quote, escape, reopen. */
function _bashSingleQuote(s: string): string {
  return "'" + s.replace(/'/g, "'\\''") + "'";
}

export function buildCurl(opts: CurlOptions): string {
  const baseUrl = (opts.baseUrl ?? "").replace(/\/+$/, "") || "http://localhost:8001";
  const url = `${baseUrl}/api/v1/workflows/${opts.workflowId}/execute`;

  const body: Record<string, unknown> = {
    trigger_payload: opts.payload ?? null,
    deterministic_mode: Boolean(opts.deterministicMode),
    sync: Boolean(opts.sync),
  };
  if (opts.sync) {
    body.sync_timeout = opts.syncTimeout ?? 120;
  }

  const lines: string[] = [
    `curl -X POST ${_bashSingleQuote(url)} \\`,
    `  -H 'Content-Type: application/json' \\`,
  ];

  if (opts.authMode === "oidc") {
    lines.push(`  -H 'Authorization: Bearer <your-oidc-access-token>' \\`);
  } else {
    const tenant = opts.tenantId || "default";
    lines.push(`  -H ${_bashSingleQuote(`X-Tenant-Id: ${tenant}`)} \\`);
  }

  lines.push(`  -d ${_bashSingleQuote(JSON.stringify(body))}`);
  return lines.join("\n");
}
