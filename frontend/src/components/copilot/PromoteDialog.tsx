/**
 * COPILOT-02.ii — the Promote confirmation modal.
 *
 * Triggered from the CopilotPanel header's "Apply" button when a
 * draft has mutations pending. The dialog is the last stop before
 * the draft lands in ``workflow_definitions`` — so it surfaces:
 *
 *   - a plain-language summary of what changed (node / edge counts
 *     relative to the base workflow if this is an edit, or "N nodes,
 *     M edges" if net-new)
 *   - the validation state (errors block; warnings + lints surface
 *     but don't block)
 *   - for net-new drafts, required name + optional description inputs
 *
 * Confirm / cancel buttons disabled while the promote call is in
 * flight. On success the parent's ``onPromoted`` receives the new
 * workflow id + version so the rest of the app (WorkflowStore) can
 * reload and route to the promoted workflow.
 *
 * User feedback shaped this: "panels should be large enough and
 * visible." The dialog is ``max-w-2xl`` (672 px) so the diff
 * summary + name field + validation list all read without
 * cramping on a 1366×768 viewport.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { CircleAlert, CircleCheck, Loader2, PlayCircle, TriangleAlert } from "lucide-react";
import {
  api,
  type CopilotDraftOut,
  type CopilotPromoteOut,
  type CopilotScenarioOut,
  type CopilotScenarioRunOut,
} from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";


interface Props {
  open: boolean;
  onClose: () => void;
  draft: CopilotDraftOut | null;
  /** Name of the base workflow if this draft forks one, for the
   * "new version" headline. Null / undefined = net-new. */
  baseWorkflowName?: string | null;
  /**
   * Node/edge counts of the base workflow so the dialog can show
   * +/- deltas. Optional — if absent, we just show the draft's
   * totals without a diff.
   */
  baseNodeCount?: number;
  baseEdgeCount?: number;
  /** Called on successful promote with the API response. The
   * caller typically reloads the workflow store + closes the
   * CopilotPanel from here. */
  onPromoted: (result: CopilotPromoteOut) => void;
}


export function PromoteDialog({
  open,
  onClose,
  draft,
  baseWorkflowName,
  baseNodeCount,
  baseEdgeCount,
  onPromoted,
}: Props) {
  const isNetNew = draft?.base_workflow_id == null;

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // COPILOT-03.e — saved scenarios + latest run results. On open
  // we fetch the list (cheap, one DB read) but never auto-run — the
  // user triggers a run explicitly because each scenario ticks
  // through the engine, which costs real tokens / API calls.
  const [scenarios, setScenarios] = useState<CopilotScenarioOut[]>([]);
  const [runResults, setRunResults] = useState<CopilotScenarioRunOut[] | null>(null);
  const [scenarioRunInFlight, setScenarioRunInFlight] = useState(false);
  const [scenariosLoadError, setScenariosLoadError] = useState<string | null>(null);
  const [confirmedFailingPromote, setConfirmedFailingPromote] = useState(false);

  // Seed the name from the draft's title whenever a new draft is
  // opened. Net-new drafts' titles are "New workflow draft" by
  // default — ok as a placeholder, user will normally rename.
  useEffect(() => {
    if (open && draft) {
      setName(isNetNew ? draft.title : baseWorkflowName ?? draft.title);
      setDescription("");
      setErrorMessage(null);
      setSubmitting(false);
      setRunResults(null);
      setConfirmedFailingPromote(false);
    }
  }, [open, draft, isNetNew, baseWorkflowName]);

  // Load scenario list on open. Runs are explicit (the "Run all"
  // button) — listing is cheap and lets us show "0 saved" vs.
  // "3 saved — run before promote".
  useEffect(() => {
    if (!open || !draft) return;
    let cancelled = false;
    setScenariosLoadError(null);
    (async () => {
      try {
        const list = await api.listDraftScenarios(draft.id);
        if (!cancelled) setScenarios(list);
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setScenariosLoadError(msg);
        setScenarios([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, draft]);

  const runAllScenarios = useCallback(async () => {
    if (!draft || scenarioRunInFlight) return;
    setScenarioRunInFlight(true);
    setErrorMessage(null);
    try {
      const result = await api.runAllDraftScenarios(draft.id);
      setRunResults(result.results);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(`Scenario run failed: ${msg}`);
    } finally {
      setScenarioRunInFlight(false);
    }
  }, [draft, scenarioRunInFlight]);

  const nodeCount = draft?.graph_json.nodes.length ?? 0;
  const edgeCount = draft?.graph_json.edges.length ?? 0;
  const nodeDelta = useMemo(() => {
    if (baseNodeCount === undefined) return null;
    return nodeCount - baseNodeCount;
  }, [nodeCount, baseNodeCount]);
  const edgeDelta = useMemo(() => {
    if (baseEdgeCount === undefined) return null;
    return edgeCount - baseEdgeCount;
  }, [edgeCount, baseEdgeCount]);

  const validation = draft?.validation;
  const errors = validation?.errors ?? [];
  const warnings = validation?.warnings ?? [];
  const lints = validation?.lints ?? [];
  const lintErrorCount = lints.filter((l) => l.severity === "error").length;
  const lintWarnCount = lints.filter((l) => l.severity === "warn").length;

  // COPILOT-03.e — derive scenario gate. If the user ran all
  // scenarios and any failed (or surfaced as stale / error), the
  // Apply button is suppressed behind a "promote anyway" confirm.
  // Scenarios that haven't been run yet don't block — we show a
  // suggestion but let the user bypass, which matches the
  // "promote gate warns, doesn't enforce" philosophy SMART-01
  // will eventually replace with a strict mode.
  const scenarioSummary = useMemo(() => {
    if (runResults === null) {
      return {
        passCount: 0,
        failCount: 0,
        staleCount: 0,
        errorCount: 0,
        hasRun: false,
      };
    }
    return {
      passCount: runResults.filter((r) => r.status === "pass").length,
      failCount: runResults.filter((r) => r.status === "fail").length,
      staleCount: runResults.filter((r) => r.status === "stale").length,
      errorCount: runResults.filter((r) => r.status === "error").length,
      hasRun: true,
    };
  }, [runResults]);

  const scenariosBlockPromote =
    scenarioSummary.hasRun &&
    (scenarioSummary.failCount > 0 ||
      scenarioSummary.staleCount > 0 ||
      scenarioSummary.errorCount > 0);

  const canSubmit =
    !!draft &&
    !submitting &&
    errors.length === 0 &&
    lintErrorCount === 0 &&
    (!isNetNew || name.trim().length > 0) &&
    (!scenariosBlockPromote || confirmedFailingPromote);

  const handleSubmit = async () => {
    if (!draft || !canSubmit) return;
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const body: {
        name?: string;
        description?: string;
        expected_version?: number;
      } = {
        expected_version: draft.version,
      };
      if (isNetNew) {
        body.name = name.trim();
        if (description.trim()) {
          body.description = description.trim();
        }
      } else if (description.trim()) {
        body.description = description.trim();
      }
      const result = await api.promoteDraft(draft.id, body);
      onPromoted(result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(msg);
    } finally {
      setSubmitting(false);
    }
  };

  if (!draft) return null;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !submitting && onClose()}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            {isNetNew ? (
              <>Promote draft to new workflow</>
            ) : (
              <>
                Promote draft as new version of{" "}
                <span className="font-mono text-sm text-muted-foreground">
                  {baseWorkflowName ?? "workflow"}
                </span>
              </>
            )}
          </DialogTitle>
        </DialogHeader>

        <ScrollArea className="flex-1 min-h-0 pr-1">
          <div className="space-y-4">
            {/* Diff summary */}
            <SummaryRow
              label="Nodes"
              current={nodeCount}
              delta={nodeDelta}
            />
            <SummaryRow
              label="Edges"
              current={edgeCount}
              delta={edgeDelta}
            />

            {/* Name (required for net-new) */}
            {isNetNew && (
              <div className="space-y-1.5">
                <Label htmlFor="promote-name" className="text-xs font-medium">
                  Workflow name
                </Label>
                <Input
                  id="promote-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Slack summariser"
                  disabled={submitting}
                  autoFocus
                />
                <p className="text-[11px] text-muted-foreground">
                  Becomes <code className="text-[10px] bg-muted px-1 rounded">workflow_definitions.name</code> — must be unique for this tenant.
                </p>
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="promote-description" className="text-xs font-medium">
                Description <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="promote-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Short description of what this workflow does"
                disabled={submitting}
              />
            </div>

            {/* Validation surface */}
            {(errors.length > 0 ||
              warnings.length > 0 ||
              lints.length > 0) && (
              <ValidationBlock
                errors={errors}
                warnings={warnings}
                lintErrorCount={lintErrorCount}
                lintWarnCount={lintWarnCount}
                lints={lints}
              />
            )}

            {validation &&
              errors.length === 0 &&
              warnings.length === 0 &&
              lintErrorCount === 0 && (
                <div className="flex items-center gap-2 text-xs text-emerald-700 dark:text-emerald-400">
                  <CircleCheck className="h-3.5 w-3.5" />
                  Validation clean
                </div>
              )}

            {/* COPILOT-03.e — saved scenarios + run gate */}
            <ScenariosBlock
              scenarios={scenarios}
              loadError={scenariosLoadError}
              runResults={runResults}
              runInFlight={scenarioRunInFlight}
              onRunAll={runAllScenarios}
              blocksPromote={scenariosBlockPromote}
              confirmed={confirmedFailingPromote}
              onToggleConfirm={() => setConfirmedFailingPromote((v) => !v)}
            />

            {/* API error from the promote call */}
            {errorMessage && (
              <div
                role="alert"
                className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive"
              >
                <p className="font-medium">Promote failed</p>
                <p className="mt-0.5 break-words">{errorMessage}</p>
              </div>
            )}
          </div>
        </ScrollArea>

        <DialogFooter className="gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="gap-1.5"
          >
            {submitting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <CircleCheck className="h-3.5 w-3.5" />
            )}
            {isNetNew ? "Create workflow" : "Save new version"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------


function SummaryRow({
  label,
  current,
  delta,
}: {
  label: string;
  current: number;
  delta: number | null;
}) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-mono text-foreground">{current}</span>
        {delta !== null && delta !== 0 && (
          <Badge
            variant="outline"
            className={
              delta > 0
                ? "text-emerald-700 border-emerald-300 dark:text-emerald-300 dark:border-emerald-800"
                : "text-amber-700 border-amber-300 dark:text-amber-300 dark:border-amber-800"
            }
          >
            {delta > 0 ? `+${delta}` : delta}
          </Badge>
        )}
      </div>
    </div>
  );
}


function ValidationBlock({
  errors,
  warnings,
  lintErrorCount,
  lintWarnCount,
  lints,
}: {
  errors: string[];
  warnings: string[];
  lintErrorCount: number;
  lintWarnCount: number;
  lints: NonNullable<CopilotDraftOut["validation"]["lints"]>;
}) {
  const hasErrors = errors.length > 0 || lintErrorCount > 0;
  return (
    <div
      className={`rounded-md border p-2.5 text-xs ${
        hasErrors
          ? "border-destructive/40 bg-destructive/5"
          : "border-amber-500/30 bg-amber-50 dark:bg-amber-950/20"
      }`}
    >
      <div className="flex items-center gap-1.5 font-medium">
        {hasErrors ? (
          <CircleAlert className="h-3.5 w-3.5 text-destructive" />
        ) : (
          <TriangleAlert className="h-3.5 w-3.5 text-amber-700 dark:text-amber-300" />
        )}
        <span>
          {errors.length > 0 && `${errors.length} error${errors.length === 1 ? "" : "s"}`}
          {errors.length > 0 && (warnings.length > 0 || lints.length > 0) && " · "}
          {warnings.length > 0 &&
            `${warnings.length} warning${warnings.length === 1 ? "" : "s"}`}
          {warnings.length > 0 && lints.length > 0 && " · "}
          {lintErrorCount > 0 &&
            `${lintErrorCount} lint error${lintErrorCount === 1 ? "" : "s"}`}
          {lintErrorCount > 0 && lintWarnCount > 0 && " · "}
          {lintWarnCount > 0 &&
            `${lintWarnCount} lint warning${lintWarnCount === 1 ? "" : "s"}`}
        </span>
      </div>

      <ul className="mt-1.5 space-y-0.5">
        {errors.map((e, i) => (
          <li key={`err-${i}`} className="text-destructive">
            • {e}
          </li>
        ))}
        {warnings.map((w, i) => (
          <li
            key={`warn-${i}`}
            className="text-amber-700 dark:text-amber-300"
          >
            • {w}
          </li>
        ))}
        {lints.map((l, i) => (
          <li
            key={`lint-${i}`}
            className={
              l.severity === "error"
                ? "text-destructive"
                : "text-amber-700 dark:text-amber-300"
            }
          >
            <span className="font-mono text-[10px] mr-1 opacity-80">
              {l.code}
            </span>
            {l.message}
            {l.fix_hint && (
              <span className="text-muted-foreground"> — {l.fix_hint}</span>
            )}
          </li>
        ))}
      </ul>

      {hasErrors && (
        <p className="mt-2 text-[11px] text-muted-foreground italic">
          Fix the errors above before promoting. Warnings and lint
          warnings won't block the promote.
        </p>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// COPILOT-03.e — saved scenarios + run-all gate
// ---------------------------------------------------------------------------


function ScenariosBlock({
  scenarios,
  loadError,
  runResults,
  runInFlight,
  onRunAll,
  blocksPromote,
  confirmed,
  onToggleConfirm,
}: {
  scenarios: CopilotScenarioOut[];
  loadError: string | null;
  runResults: CopilotScenarioRunOut[] | null;
  runInFlight: boolean;
  onRunAll: () => void;
  blocksPromote: boolean;
  confirmed: boolean;
  onToggleConfirm: () => void;
}) {
  // Nothing to show — tidy hide, don't clutter the dialog.
  if (scenarios.length === 0 && !loadError) {
    return null;
  }

  if (loadError) {
    return (
      <div
        role="alert"
        className="rounded-md border border-amber-500/30 bg-amber-50 dark:bg-amber-950/20 p-2.5 text-xs"
      >
        <p className="font-medium text-amber-700 dark:text-amber-300">
          Couldn't load saved scenarios
        </p>
        <p className="mt-0.5 break-words text-muted-foreground">{loadError}</p>
      </div>
    );
  }

  // Map results by scenario_id so the list renders in scenario order
  // (created_at) rather than run-result order.
  const resultById = new Map(
    (runResults ?? []).map((r) => [r.scenario_id, r]),
  );

  const passCount = (runResults ?? []).filter((r) => r.status === "pass").length;
  const failCount = (runResults ?? []).filter((r) => r.status === "fail").length;
  const staleCount = (runResults ?? []).filter((r) => r.status === "stale").length;
  const errorCount = (runResults ?? []).filter((r) => r.status === "error").length;

  return (
    <div className="rounded-md border p-2.5 text-xs space-y-2">
      <div className="flex items-center justify-between">
        <div className="font-medium">
          Saved scenarios ({scenarios.length})
        </div>
        <button
          type="button"
          onClick={onRunAll}
          disabled={runInFlight}
          className="inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] hover:bg-accent disabled:opacity-50"
          aria-label="Run all scenarios"
        >
          {runInFlight ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <PlayCircle className="h-3 w-3" />
          )}
          Run all
        </button>
      </div>

      {runResults !== null && (
        <p className="text-[11px] text-muted-foreground">
          {passCount} pass · {failCount} fail
          {staleCount > 0 && ` · ${staleCount} stale`}
          {errorCount > 0 && ` · ${errorCount} error`}
        </p>
      )}

      <ul className="space-y-1">
        {scenarios.map((s) => {
          const r = resultById.get(s.scenario_id);
          return (
            <li
              key={s.scenario_id}
              className="flex items-start justify-between gap-2"
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-[11px]">{s.name}</div>
                {r && r.status === "fail" && r.mismatches.length > 0 && (
                  <div className="text-[10px] text-muted-foreground truncate">
                    {r.mismatches.length} mismatch
                    {r.mismatches.length === 1 ? "" : "es"}
                  </div>
                )}
                {r && r.status === "error" && r.message && (
                  <div className="text-[10px] text-destructive truncate">
                    {r.message}
                  </div>
                )}
              </div>
              {r ? <ScenarioStatusBadge status={r.status} /> : (
                <Badge variant="outline" className="text-muted-foreground">
                  not run
                </Badge>
              )}
            </li>
          );
        })}
      </ul>

      {blocksPromote && (
        <label className="flex items-start gap-2 pt-2 border-t cursor-pointer">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={onToggleConfirm}
            className="mt-0.5"
          />
          <span className="text-[11px] leading-relaxed">
            Promote anyway — I've reviewed the{" "}
            {failCount + staleCount + errorCount} failing scenario
            {failCount + staleCount + errorCount === 1 ? "" : "s"} and
            still want to promote. Use this sparingly.
          </span>
        </label>
      )}
    </div>
  );
}


function ScenarioStatusBadge({
  status,
}: {
  status: "pass" | "fail" | "stale" | "error";
}) {
  if (status === "pass") {
    return (
      <Badge
        variant="outline"
        className="text-emerald-700 border-emerald-300 dark:text-emerald-300 dark:border-emerald-800"
      >
        pass
      </Badge>
    );
  }
  if (status === "fail") {
    return (
      <Badge variant="outline" className="text-destructive border-destructive/40">
        fail
      </Badge>
    );
  }
  if (status === "stale") {
    return (
      <Badge
        variant="outline"
        className="text-amber-700 border-amber-300 dark:text-amber-300 dark:border-amber-800"
      >
        stale
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      error
    </Badge>
  );
}
