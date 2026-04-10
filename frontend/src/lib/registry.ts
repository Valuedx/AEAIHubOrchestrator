import type { PaletteItem, NodeCategory } from "@/types/nodes";
import registryData from "../../../shared/node_registry.json";

interface RegistryNodeType {
  type: string;
  category: string;
  label: string;
  description: string;
  icon: string;
  config_schema: Record<string, { type: string; default?: unknown }>;
}

interface Registry {
  version: string;
  categories: { id: string; label: string; description: string }[];
  node_types: RegistryNodeType[];
}

const registry = registryData as unknown as Registry;

function schemaToDefaultConfig(
  schema: Record<string, { type: string; default?: unknown }>,
): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  for (const [key, def] of Object.entries(schema)) {
    if (def.default !== undefined) {
      config[key] = def.default;
    } else if (def.type === "string") {
      config[key] = "";
    } else if (def.type === "number" || def.type === "integer") {
      config[key] = 0;
    } else if (def.type === "boolean") {
      config[key] = false;
    } else if (def.type === "object") {
      config[key] = {};
    } else if (def.type === "array") {
      config[key] = [];
    }
  }
  return config;
}

export const REGISTRY_PALETTE: PaletteItem[] = registry.node_types.map(
  (nt) => ({
    nodeCategory: nt.category as NodeCategory,
    label: nt.label,
    description: nt.description,
    icon: nt.icon,
    defaultConfig: schemaToDefaultConfig(nt.config_schema),
  }),
);

export const REGISTRY_CATEGORIES = registry.categories;

export function getConfigSchema(
  label: string,
): Record<string, { type: string; default?: unknown; enum?: unknown[]; min?: number; max?: number }> | null {
  const nt = registry.node_types.find((n) => n.label === label);
  return nt ? (nt.config_schema as ReturnType<typeof getConfigSchema>) : null;
}

export function getRegistryNodeType(label: string): RegistryNodeType | null {
  return registry.node_types.find((n) => n.label === label) ?? null;
}
