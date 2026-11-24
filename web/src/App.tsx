import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";
import { api, errorDetail, setUnauthorizedHandler } from "./api";
import type { AuthState } from "./api";
import { Shell } from "./components/Shell";
import { WallScreen } from "./modules/wall/WallScreen";
import { ThemeContext, useThemeController } from "./hooks/useTheme";
import { useToast } from "./hooks/useToast";
import { runSessionEndHooks } from "./lib/session";
import { SetupScreen } from "./screens/SetupScreen";
import { SignInScreen } from "./screens/SignInScreen";
import { UnreachableScreen } from "./screens/UnreachableScreen";
import { WelcomeGate } from "./setup/WelcomeGate";

const AUTH_QUERY_KEY = ["auth"] as const;

/** Decides between first-run setup, sign-in, the welcome steps and the app itself. */
export function App() {
  const client = useQueryClient();
  const theme = useThemeController();
  const toast = useToast();
  const auth = useQuery({ queryKey: AUTH_QUERY_KEY, queryFn: api.authState, retry: 1 });

  // Drops everything fetched for the previous session and shows sign-in.
  const endSession = useCallback(() => {
    runSessionEndHooks();
    client.setQueryData<AuthState>(AUTH_QUERY_KEY, (state) => (state ? { ...state, signed_in: false } : state));
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== AUTH_QUERY_KEY[0] });
  }, [client]);

  // Any 401 from an authed endpoint means the session ended.
  useEffect(() => {
    setUnauthorizedHandler(endSession);
    return () => setUnauthorizedHandler(null);
  }, [endSession]);

  const refreshAuth = useCallback(async () => {
    await client.invalidateQueries({ queryKey: AUTH_QUERY_KEY });
  }, [client]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
      endSession();
    } catch (error) {
      toast.error(errorDetail(error));
    }
  }, [endSession, toast]);

  let content;
  if (auth.isPending) {
    content = (
      <div className="flex h-full items-center justify-center bg-bg text-sm text-text-faint" role="status">
        Loading…
      </div>
    );
  } else if (auth.isError) {
    content = <UnreachableScreen detail={errorDetail(auth.error)} onRetry={() => void auth.refetch()} busy={auth.isFetching} />;
  } else if (auth.data.setup_needed) {
    content = <SetupScreen onDone={refreshAuth} />;
  } else if (!auth.data.signed_in) {
    content = <SignInScreen ownerName={auth.data.owner_name} onDone={refreshAuth} />;
  } else if (window.location.pathname.replace(/\/+$/, "") === "/wall") {
    // A full-screen status board for a second monitor; no shell around it.
    content = <WallScreen />;
  } else {
    // Until the welcome steps are finished (or skipped), they come first.
    content = (
      <WelcomeGate>
        <Shell ownerName={auth.data.owner_name} onSignOut={signOut} />
      </WelcomeGate>
    );
  }

  return <ThemeContext.Provider value={theme}>{content}</ThemeContext.Provider>;
}
