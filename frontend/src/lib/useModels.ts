/**
 * useModels
 *
 * MODEL-01.e — single hook that hydrates the tenant-filtered model
 * catalogue from `/api/v1/models` and caches it at module scope so
 * every picker (Node Inspector, copilot session, KB dialog, template
 * resolver) sees a consistent list.
 *
 * The cache is keyed by (kind, provider, includePreview, copilotOnly)
 * so toggling those filters doesn't re-fetch unnecessarily.
 *
 * Refetch is intentionally opt-in: dialogs fetch once on mount, and a
 * tenant-policy update triggers a manual invalidation from the
 * settings dialog rather than a global refetch on every render.
 */

import { useEffect, useState } from "react";
import type { Edge, Node } from "@xyflow/react";
import { api, type ModelsOut, type ModelDefaultsOut } from "@/lib/api";
import {
  TEMPLATE_TIER_FAST,
  TEMPLATE_TIER_BALANCED,
  TEMPLATE_TIER_POWERFUL,
} from "@/lib/modelTiers";

type Key = string;

function cacheKey(opts: Options): Key {
  return [
    opts.kind ?? "all",
    opts.provider ?? "",
    opts.includePreview === false ? "0" : "1",
    opts.copilotOnly ? "copilot" : "any",
  ].join("|");
}

interface Options {
  kind?: "llm" | "embedding" | "all";
  provider?: string;
  includePreview?: boolean;
  copilotOnly?: boolean;
}

const _cache = new Map<Key, Promise<ModelsOut>>();
let _defaultsCache: Promise<ModelDefaultsOut> | null = null;
// Synchronous snapshot of the last-resolved defaults — useful for
// non-hook call sites like ``workflowStore.loadTemplate`` that can't
// await. Populated the first time ``useModelDefaults`` or
// ``prefetchModelDefaults`` resolves. Null until warm.
let _defaultsSnapshot: ModelDefaultsOut | null = null;

/** Invalidate every cached catalogue — called after a tenant-policy
 *  PATCH that touches model overrides. */
export function invalidateModelsCache(): void {
  _cache.clear();
  _defaultsCache = null;
  _defaultsSnapshot = null;
}

/** Sync accessor for the last-resolved tenant defaults. Returns null
 *  until the first fetch completes. Used by ``resolveTemplateTiers``
 *  when ``loadTemplate`` fires before the hook mounts. */
export function getCachedModelDefaults(): ModelDefaultsOut | null {
  return _defaultsSnapshot;
}

/** Fire-and-forget prefetch — call once on app mount so templates
 *  loaded early have tenant defaults available. */
export function prefetchModelDefaults(): void {
  void fetchDefaults().catch(() => {
    /* swallowed — the next hook mount will retry */
  });
}

async function fetchModels(opts: Options): Promise<ModelsOut> {
  const key = cacheKey(opts);
  let p = _cache.get(key);
  if (!p) {
    p = api.getModels(opts);
    _cache.set(key, p);
    p.catch(() => {
      // Evict a failed request so the next caller retries.
      _cache.delete(key);
    });
  }
  return p;
}

async function fetchDefaults(): Promise<ModelDefaultsOut> {
  if (!_defaultsCache) {
    _defaultsCache = api.getModelDefaults().then((d) => {
      _defaultsSnapshot = d;
      return d;
    });
    _defaultsCache.catch(() => {
      _defaultsCache = null;
    });
  }
  return _defaultsCache;
}


// ---------------------------------------------------------------------------
// Template tier resolution (MODEL-01.f)
// ---------------------------------------------------------------------------

interface TierPair {
  provider: string;
  model: string;
}

/** Match a node config's (provider, model) against a known tier marker.
 *  Returns the tier name if it matches, or null. */
function matchTier(
  provider: unknown,
  model: unknown,
): "fast" | "balanced" | "powerful" | null {
  if (typeof provider !== "string" || typeof model !== "string") return null;
  if (
    provider === TEMPLATE_TIER_FAST.provider &&
    model === TEMPLATE_TIER_FAST.model
  )
    return "fast";
  if (
    provider === TEMPLATE_TIER_BALANCED.provider &&
    model === TEMPLATE_TIER_BALANCED.model
  )
    return "balanced";
  if (
    provider === TEMPLATE_TIER_POWERFUL.provider &&
    model === TEMPLATE_TIER_POWERFUL.model
  )
    return "powerful";
  return null;
}

/** Walk a template graph and swap TIER-marked node configs for the
 *  tenant's resolved defaults. Node configs whose (provider, model)
 *  don't match a known tier are left untouched — user-customised
 *  templates or back-compat entries keep working. */
export function resolveTemplateTiers(
  graph: { nodes: Node[]; edges: Edge[] },
  defaults: ModelDefaultsOut | null,
): { nodes: Node[]; edges: Edge[] } {
  if (!defaults) return graph; // cache not warm — use template literals
  const tierMap: Record<"fast" | "balanced" | "powerful", TierPair> = {
    fast: { provider: defaults.fast.provider, model: defaults.fast.model_id },
    balanced: {
      provider: defaults.balanced.provider,
      model: defaults.balanced.model_id,
    },
    powerful: {
      provider: defaults.powerful.provider,
      model: defaults.powerful.model_id,
    },
  };
  const nodes = graph.nodes.map((n) => {
    const data = (n.data ?? {}) as { config?: Record<string, unknown> };
    const config = data.config;
    if (!config) return n;
    const tier = matchTier(config.provider, config.model);
    if (!tier) return n;
    const swap = tierMap[tier];
    // Only swap when the tenant pin actually differs — otherwise keep
    // object identity so React Flow doesn't see a "change".
    if (swap.provider === config.provider && swap.model === config.model) {
      return n;
    }
    return {
      ...n,
      data: {
        ...data,
        config: { ...config, provider: swap.provider, model: swap.model },
      },
    } as Node;
  });
  return { nodes, edges: graph.edges };
}

export interface UseModelsResult {
  data: ModelsOut | null;
  error: Error | null;
  loading: boolean;
}

export function useModels(opts: Options = {}): UseModelsResult {
  const [data, setData] = useState<ModelsOut | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  // Re-fetch when any filter changes. Stringify the opts for a stable
  // dependency without re-creating arrays.
  const key = cacheKey(opts);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchModels(opts)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e : new Error(String(e)));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { data, error, loading };
}

export interface UseModelDefaultsResult {
  data: ModelDefaultsOut | null;
  error: Error | null;
  loading: boolean;
}

export function useModelDefaults(): UseModelDefaultsResult {
  const [data, setData] = useState<ModelDefaultsOut | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    fetchDefaults()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e : new Error(String(e)));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { data, error, loading };
}
