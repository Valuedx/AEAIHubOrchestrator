/**
 * MODEL-01.f — tier-based provider/model defaults for starter templates.
 *
 * These constants live in their own module (not inside `templates/index.ts`)
 * so the example workflows (which are imported BY `templates/index.ts`)
 * can reference them without creating a circular import — a cycle that
 * breaks Vite's ESM hoisting at dev-time with a
 * "Cannot access 'TEMPLATE_TIER_FAST' before initialization" error.
 *
 * Templates reference these constants via spread (``...TEMPLATE_TIER_FAST``)
 * so ``resolveTemplateTiers`` can detect a tier at load time and swap the
 * provider/model for the tenant's pin (see ``useModels.ts``). The literal
 * values match the registry's ``fast`` / ``balanced`` / ``powerful`` tier
 * defaults so a template loaded before the cache is warm still runs on a
 * valid registry model.
 *
 * Marker shape stays ``{provider, model}`` (no extra marker field) so the
 * config is identical to a normal config — templates remain portable
 * graph_json that the API consumes without any special-casing.
 */

export const TEMPLATE_TIER_FAST = Object.freeze({
  provider: "google",
  model: "gemini-2.5-flash",
});

export const TEMPLATE_TIER_BALANCED = Object.freeze({
  provider: "google",
  model: "gemini-2.5-pro",
});

export const TEMPLATE_TIER_POWERFUL = Object.freeze({
  provider: "google",
  model: "gemini-3.1-pro-preview",
});

export type TemplateTier = "fast" | "balanced" | "powerful";
