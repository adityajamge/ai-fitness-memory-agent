/**
 * Routes — DESIGN.md §6.13. Four, and that is the whole product.
 *
 * **Every route is lazy.** The landing page is the first paint a judge sees, and eagerly bundling
 * the app shell would make a marketing visitor download the glass box, the dialog primitives, and
 * the query layer before reading a headline. Splitting here is what keeps the initial chunk near
 * the §10 budget instead of over it — measured, not assumed.
 *
 * The landing page stays inside this SPA rather than becoming a separate static page: a separate
 * page would paint faster still, but it would fork the design system into two places that drift,
 * and drift is what DESIGN.md exists to prevent.
 */

import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router";

const Landing = lazy(() => import("./routes/Landing").then((m) => ({ default: m.Landing })));
const AppScreen = lazy(() =>
  import("./routes/AppScreen").then((m) => ({ default: m.AppScreen })),
);
const AuthScreen = lazy(() =>
  import("./routes/AuthScreen").then((m) => ({ default: m.AuthScreen })),
);
const NotFound = lazy(() => import("./routes/NotFound").then((m) => ({ default: m.NotFound })));

/** Deliberately blank, not a spinner. These chunks resolve in tens of milliseconds on a warm
 * cache, and a spinner that flashes for 40ms reads as jank rather than progress (§6.10). */
const RouteFallback = () => <div className="min-h-dvh bg-background" />;

export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/app" element={<AppScreen />} />
        <Route path="/login" element={<AuthScreen mode="login" />} />
        <Route path="/signup" element={<AuthScreen mode="signup" />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
