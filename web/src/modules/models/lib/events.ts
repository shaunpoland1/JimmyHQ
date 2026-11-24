// Which cached model data a live Tape event makes stale.

export type ModelData = "providers" | "roles" | "health" | "usage" | "prices";

/** The parts of the models data to fetch again after an event of this kind. */
export function staleAfter(kind: string, payload: Record<string, unknown> = {}): ModelData[] {
  switch (kind) {
    case "model.called":
    case "model.failed":
      // A real call also updates the provider's stored health.
      return ["usage", "health", "providers"];
    case "model.provider.saved":
    case "model.provider.removed":
      return ["providers", "health"];
    case "model.role.set":
      return ["roles"];
    case "model.prices.set":
      return ["prices", "usage"];
    case "setting.updated":
    case "setting.reset": {
      const key = typeof payload.key === "string" ? payload.key : "";
      if (key === "models.savings_reference" || key === "models.currency") return ["usage"];
      if (key === "models.health_interval_s") return ["health"];
      return [];
    }
    default:
      return [];
  }
}
