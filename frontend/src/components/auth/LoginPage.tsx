/**
 * LoginPage — shown when no auth token is stored and VITE_AUTH_MODE is
 * either "oidc" or "local".
 *
 * OIDC mode redirects the browser to the backend OIDC login endpoint,
 * which in turn redirects to the configured identity provider.
 *
 * Local mode renders a username/password form that POSTs to
 * /auth/local/login and stores the returned JWT in sessionStorage via
 * setAuthToken(). Active Directory / LDAP binding is out of scope for
 * this revision — when it ships, it will go through the same endpoint
 * and this page won't need to change.
 */

import { useState } from "react";

import { loginLocal } from "@/lib/api";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8001";
const AUTH_MODE = (import.meta.env.VITE_AUTH_MODE as string | undefined) ?? "";
const DEFAULT_TENANT = (import.meta.env.VITE_TENANT_ID as string | undefined) ?? "default";

export function LoginPage() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <div className="flex flex-col items-center gap-6 p-10 rounded-xl border bg-sidebar shadow-md max-w-sm w-full">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded bg-primary flex items-center justify-center">
            <span className="text-sm font-bold text-primary-foreground">AE</span>
          </div>
          <span className="text-lg font-semibold">AI Hub</span>
        </div>

        {AUTH_MODE === "local" ? <LocalLoginForm /> : <OidcSignInButton />}
      </div>
    </div>
  );
}

function OidcSignInButton() {
  const handleLogin = () => {
    window.location.href = `${API_BASE}/auth/oidc/login`;
  };
  return (
    <>
      <div className="text-center space-y-1">
        <h1 className="text-base font-medium">Sign in to continue</h1>
        <p className="text-sm text-muted-foreground">
          Authentication is required to access the orchestrator.
        </p>
      </div>
      <button
        onClick={handleLogin}
        className="w-full rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
      >
        Sign in with SSO
      </button>
      <p className="text-[10px] text-muted-foreground text-center">
        You will be redirected to your organization's identity provider.
      </p>
    </>
  );
}

function LocalLoginForm() {
  const [tenantId, setTenantId] = useState(DEFAULT_TENANT);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await loginLocal(tenantId.trim(), username.trim(), password);
      // Reload so every module that read the stale-no-token state
      // (App gate, api.ts's in-memory header cache, etc.) re-initialises.
      window.location.reload();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // The backend returns a single "Invalid credentials" body for any
      // login failure; surface that verbatim and fall back for network
      // errors.
      setError(msg.includes("401") ? "Invalid credentials" : "Login failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="w-full space-y-4">
      <div className="text-center space-y-1">
        <h1 className="text-base font-medium">Sign in</h1>
        <p className="text-sm text-muted-foreground">Use your local credentials.</p>
      </div>

      <div className="space-y-2">
        <label className="text-xs font-medium text-muted-foreground" htmlFor="tenant_id">
          Tenant
        </label>
        <input
          id="tenant_id"
          name="tenant_id"
          type="text"
          autoComplete="organization"
          value={tenantId}
          onChange={(e) => setTenantId(e.target.value)}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          required
        />
      </div>

      <div className="space-y-2">
        <label className="text-xs font-medium text-muted-foreground" htmlFor="username">
          Username
        </label>
        <input
          id="username"
          name="username"
          type="text"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          required
        />
      </div>

      <div className="space-y-2">
        <label className="text-xs font-medium text-muted-foreground" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          required
        />
      </div>

      {error && (
        <p className="text-xs text-destructive text-center" role="alert">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="w-full rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
      >
        {submitting ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
