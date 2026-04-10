/**
 * LoginPage — shown when VITE_AUTH_MODE=oidc and no token is stored.
 *
 * Clicking "Sign in with SSO" redirects to the backend OIDC login endpoint,
 * which in turn redirects to the configured identity provider.
 *
 * After the OIDC callback the backend returns an internal JWT. The callback
 * URL can be a separate page that stores the token and redirects back, or
 * the user can paste it manually in development. In production, configure
 * VITE_OIDC_CALLBACK_URL to point at a thin redirect page.
 */

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8001";

export function LoginPage() {
  const handleLogin = () => {
    window.location.href = `${API_BASE}/auth/oidc/login`;
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <div className="flex flex-col items-center gap-6 p-10 rounded-xl border bg-sidebar shadow-md max-w-sm w-full">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded bg-primary flex items-center justify-center">
            <span className="text-sm font-bold text-primary-foreground">AE</span>
          </div>
          <span className="text-lg font-semibold">AI Hub</span>
        </div>

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
      </div>
    </div>
  );
}
