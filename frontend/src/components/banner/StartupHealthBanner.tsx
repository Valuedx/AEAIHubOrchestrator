/**
 * STARTUP-01 — renders `/health/ready` outcomes at the top of the
 * app when any check is ``warn`` or ``fail``.
 *
 * The body of the banner is deliberately compact — headline + per-
 * failed-check one-liner with its remediation — so operators can
 * diagnose config issues without opening a terminal. Dismissible
 * for the session so a known warn (e.g. dev-mode RLS posture)
 * doesn't nag a developer all day.
 *
 * Two render branches so the UI tone matches severity:
 *
 *   - ``fail`` → red strip, `role="alert"`, non-dismissible (a
 *     failing readiness check is the orchestrator announcing it
 *     can't serve traffic properly; hiding that is wrong).
 *   - ``warn`` → amber strip, dismissible for the tab's session.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronUp, X, XCircle } from "lucide-react";
import { api, type HealthReadyOut, type StartupCheck } from "@/lib/api";

const DISMISS_KEY = "aeai:startup-health:dismissed-at";
// Keep a dismiss sticky for one hour — long enough to leave the
// operator alone during a session, short enough that the next work
// day re-surfaces real problems.
const DISMISS_TTL_MS = 60 * 60 * 1000;


export function StartupHealthBanner() {
  const [health, setHealth] = useState<HealthReadyOut | null>(null);
  const [dismissed, setDismissed] = useState<boolean>(() => _isDismissedRecently());
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getHealthReady()
      .then((h) => {
        if (!cancelled) setHealth(h);
      })
      .catch(() => {
        // Network failure — silently skip; a banner about "couldn't
        // load the banner" is noise operators don't need.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleDismiss = useCallback(() => {
    try {
      window.localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      /* localStorage unavailable — dismiss still works for the session */
    }
    setDismissed(true);
  }, []);

  if (!health) return null;
  if (health.status === "pass") return null;
  if (health.status === "warn" && dismissed) return null;

  const problems = health.checks.filter(
    (c) => c.status === "fail" || c.status === "warn",
  );

  if (problems.length === 0) return null;

  const isFail = health.status === "fail";
  const palette = isFail
    ? "border-destructive/50 bg-destructive/10 text-destructive"
    : "border-amber-500/40 bg-amber-50 dark:bg-amber-950/30 text-amber-900 dark:text-amber-100";
  const Icon = isFail ? XCircle : AlertTriangle;

  return (
    <div
      role={isFail ? "alert" : "status"}
      className={`shrink-0 border-b px-3 py-2 text-sm ${palette}`}
    >
      <div className="flex items-start gap-2">
        <Icon className="h-4 w-4 mt-0.5 shrink-0" aria-hidden />
        <div className="flex-1 min-w-0">
          <p className="font-medium">
            {isFail
              ? `Readiness failing — ${problems.length} check${problems.length === 1 ? "" : "s"} need attention`
              : `${problems.length} readiness check${problems.length === 1 ? "" : "s"} in warn state`}
          </p>
          {!expanded && (
            <p className="text-[12px] opacity-80 mt-0.5">
              {problems.map((c) => c.name).join(", ")}
              <button
                type="button"
                className="ml-2 underline underline-offset-2 hover:no-underline"
                onClick={() => setExpanded(true)}
              >
                show details
              </button>
            </p>
          )}
          {expanded && (
            <ul className="mt-1.5 space-y-1.5 text-[12px]">
              {problems.map((c) => (
                <CheckDetail key={c.name} check={c} isFail={isFail} />
              ))}
            </ul>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            className="p-1 rounded hover:bg-black/5 dark:hover:bg-white/10"
            onClick={() => setExpanded((v) => !v)}
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </button>
          {/* Fail banners are intentionally non-dismissible — a failing
              readiness probe means the app can't serve properly, and
              hiding that from the operator is wrong. */}
          {!isFail && (
            <button
              type="button"
              className="p-1 rounded hover:bg-black/5 dark:hover:bg-white/10"
              onClick={handleDismiss}
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}


function CheckDetail({ check, isFail }: { check: StartupCheck; isFail: boolean }) {
  const statusPill =
    check.status === "fail"
      ? "bg-destructive/20 text-destructive"
      : "bg-amber-500/20 text-amber-900 dark:text-amber-100";
  return (
    <li className={`rounded ${isFail ? "" : ""}`}>
      <div className="flex items-center gap-2">
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${statusPill}`}>
          {check.status}
        </span>
        <span className="font-mono text-[11px]">{check.name}</span>
      </div>
      <p className="mt-0.5 opacity-90">{check.message}</p>
      {check.remediation && (
        <p className="mt-0.5 text-[11px] opacity-80">
          <span className="font-medium">Fix:</span> {check.remediation}
        </p>
      )}
    </li>
  );
}


function _isDismissedRecently(): boolean {
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY);
    if (!raw) return false;
    const ts = Number(raw);
    if (!Number.isFinite(ts)) return false;
    return Date.now() - ts < DISMISS_TTL_MS;
  } catch {
    return false;
  }
}
