import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, errorDetail } from "../api";
import { SETTINGS_QUERY_KEY, useSettingsQuery } from "../hooks/useSettings";
import {
  NAME_SETTING_KEY,
  WELCOME_SETTING_KEY,
  adoptsExistingSetup,
  chatModelReady,
  parseProgress,
  previousStep,
  progressAfter,
  stepLabel,
  welcomeStep,
} from "../lib/welcome";
import type { WelcomeStep } from "../lib/welcome";
import { useProvidersQuery, useRolesQuery } from "../modules/models/queries";
import { WelcomeAbout } from "./WelcomeAbout";
import { WelcomeCard } from "./WelcomeCard";
import { WelcomeFinish } from "./WelcomeFinish";
import { WelcomeModel } from "./WelcomeModel";

const TITLES: Record<WelcomeStep, { title: string; subtitle: string }> = {
  about: { title: "About you", subtitle: "A few things JimmyHQ needs to get days and weeks right. You can change them later in Settings." },
  model: {
    title: "Connect a model",
    subtitle: "Chat, memory and agents all need a model to think with. Add the model server you run, or a hosted service you have a key for, then choose the model chat uses.",
  },
  finish: { title: "You're set", subtitle: "Everything you chose is saved. Here are a few good places to start, and the Apps screen is where the rest of JimmyHQ lives." },
};

/**
 * Shows the welcome steps instead of the app until they are finished. Progress
 * is the `setup.welcome` setting, so it resumes on any device.
 */
export function WelcomeGate({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const settings = useSettingsQuery();
  const providers = useProvidersQuery();
  const roles = useRolesQuery();
  // Back moves between steps without changing what is saved.
  const [viewing, setViewing] = useState<WelcomeStep | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const adopted = useRef(false);

  const stored = settings.data?.settings.find((item) => item.key === WELCOME_SETTING_KEY);
  const progress = parseProgress(stored?.value);
  const chatReady = providers.data && roles.data ? chatModelReady(roles.data.roles, providers.data.providers) : providers.isError || roles.isError ? null : undefined;
  const known = chatReady === undefined ? null : chatReady;
  const due = stored ? welcomeStep(progress, known) : null;

  // An installation set up before these steps existed is marked finished once.
  useEffect(() => {
    if (!stored || adopted.current || !adoptsExistingSetup(progress, known)) return;
    adopted.current = true;
    void api.putSetting(WELCOME_SETTING_KEY, "done").then(
      () => client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY }),
      () => undefined, // Harmless: it is decided the same way next time.
    );
  }, [stored, progress, known, client]);

  // Wait for what decides it; a settings failure never keeps the owner out.
  if (settings.isPending || (stored && progress === "new" && chatReady === undefined)) {
    return (
      <div className="flex h-full items-center justify-center bg-bg text-sm text-text-faint" role="status">
        Loading…
      </div>
    );
  }
  if (!due) return <>{children}</>;

  const step = viewing ?? due;

  /** Saves values in order, then the progress, and moves on. */
  const saveStep = async (from: WelcomeStep, values: [string, string | boolean][] = []) => {
    setBusy(true);
    setFailure(null);
    try {
      for (const [key, value] of values) await api.putSetting(key, value);
      await api.putSetting(WELCOME_SETTING_KEY, progressAfter(from));
      if (values.some(([key]) => key === NAME_SETTING_KEY)) void client.invalidateQueries({ queryKey: ["auth"] });
      await client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
      setViewing(null);
    } catch (error) {
      setFailure(`${errorDetail(error)} Nothing after that was saved; try again.`);
    } finally {
      setBusy(false);
    }
  };

  const back = () => {
    setFailure(null);
    setViewing(previousStep(step));
  };

  const skipAll = async () => {
    setBusy(true);
    setFailure(null);
    try {
      await api.putSetting(WELCOME_SETTING_KEY, "done");
      await client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
    } catch (error) {
      setFailure(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  const footer =
    step === "finish" ? null : (
      <>
        You can change all of this later in Settings.{" "}
        <button type="button" className="underline underline-offset-2 hover:text-text" onClick={() => void skipAll()} disabled={busy}>
          Skip the welcome steps
        </button>
      </>
    );

  return (
    <WelcomeCard step={stepLabel(step)} title={TITLES[step].title} subtitle={TITLES[step].subtitle} wide={step === "model"} footer={footer}>
      {step === "about" ? (
        <WelcomeAbout settings={settings.data?.settings ?? []} busy={busy} failure={failure} onSave={(values) => void saveStep("about", values)} />
      ) : step === "model" ? (
        <WelcomeModel busy={busy} failure={failure} onBack={back} onDone={() => void saveStep("model")} />
      ) : (
        <WelcomeFinish chatReady={chatReady === true} busy={busy} failure={failure} onBack={back} onFinish={() => void saveStep("finish")} />
      )}
    </WelcomeCard>
  );
}
