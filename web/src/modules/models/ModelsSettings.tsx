import { ProvidersSection } from "./ProvidersSection";
import { useModelsLive, useProvidersQuery, useRolesQuery } from "./queries";
import { RolesSection } from "./RolesSection";
import { UsageSection } from "./UsageSection";

/** The "Models and roles" settings section. Owned by the models module. */
export function ModelsSettings() {
  useModelsLive();
  const providers = useProvidersQuery();
  const roles = useRolesQuery();

  // Its own container: breakpoints follow the width this section really has.
  return (
    <div className="@container min-w-0">
      <p className="max-w-[62ch] text-sm text-text-muted">
        Connect the providers you have, then point each role at a model. Agents and apps ask for a role, never a specific model, so you can swap models without touching them.
      </p>
      <ProvidersSection roles={roles.data?.roles} />
      <RolesSection roles={roles} providers={providers.data?.providers} />
      <UsageSection providers={providers.data?.providers} />
    </div>
  );
}
