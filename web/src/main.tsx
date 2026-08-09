/**
 * App entry. Three providers, in this order, and no more.
 *
 * `LazyMotion` with the `domAnimation` feature set is what keeps Motion at roughly 5 KB instead
 * of ~31 KB — it matters because the landing page is the first paint a judge sees. Components
 * must therefore use the `m.*` namespace, never `motion.*`, or the lazy features are bypassed.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { LazyMotion, domAnimation } from "motion/react";
import { BrowserRouter } from "react-router";

import App from "./App";
import { isUnauthorized } from "./api/queries";
import { markSessionExpired } from "./session/sessionStore";
import "./styles/theme.css";
// Side-effecting import: registers the system-theme-change listener immediately rather than
// whenever a lazy route first renders a `ThemeToggle`. index.html's inline script already
// prevents the first-paint flash; this just makes an OS theme flip (while nobody has toggled
// manually) take effect right away instead of only on the next route/component mount.
import "./theme/themeStore";

/**
 * Every 401 in the app funnels here, whether it came from a query or a mutation. Catching it
 * centrally is what lets §6.11.1 hold: one notice, one re-auth dialog, and no component has to
 * remember to handle expiry itself.
 */
const onAuthError = (error: unknown) => {
  if (isUnauthorized(error)) markSessionExpired();
};

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onAuthError }),
  mutationCache: new MutationCache({ onError: onAuthError }),
  defaultOptions: {
    queries: {
      // Glass-box data is immutable once written: a persisted trace never changes (I-29), and a
      // memory row only changes when superseded. Refetching on every focus would be pure waste.
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      // A 404 here means "does not exist, or is not yours" (I-28) and retrying cannot change
      // that. Only retry what might genuinely be transient.
      retry: (failureCount, error) => {
        const status = (error as { status?: number })?.status;
        if (status && status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
    },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("#root missing from index.html");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LazyMotion features={domAnimation} strict>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </LazyMotion>
    </QueryClientProvider>
  </StrictMode>,
);
