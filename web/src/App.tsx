/**
 * Routes — DESIGN.md §6.13. Deliberately few, and that is the whole product.
 *
 * **Three destinations** (2026-08-13 IA revision, §16 Decisions Log): `/app` (Chat, and the
 * default landing page for every account — "talk to your health memory" is the core promise),
 * `/app/review` (the memory briefing), `/app/profile` (identity/goals/account). A brand-new
 * account lands in `/app` after onboarding too, because the guided first turn is the live
 * product experience (§9.1) — both entry points land on the same surface now.
 *
 * **Every route is lazy.** The landing page is the first paint a judge sees, and eagerly bundling
 * the app shell would make a marketing visitor download the glass box and the query layer before
 * reading a headline. Splitting here is what keeps the initial chunk near the §10 budget instead
 * of over it — measured, not assumed.
 *
 * The landing page stays inside this SPA rather than becoming a separate static page: a separate
 * page would paint faster still, but it would fork the design system into two places that drift,
 * and drift is what DESIGN.md exists to prevent.
 *
 * **`/app/profile` is a plain route, on every breakpoint.** Through 2026-08-12 the top bar's
 * account icon opened it as a dialog over the current screen on desktop (`state.backgroundLocation`
 * plus a second layered `Routes`, the React Router "modal route" recipe). That is retired: Profile
 * is now a primary nav destination reached from the top bar like Chat or Review (§6.19/§6.13), and
 * a primary destination is navigated to, not popped open over another screen.
 */

import { Suspense, lazy } from "react";
import { Route, Routes } from "react-router";

const Landing = lazy(() => import("./routes/Landing").then((m) => ({ default: m.Landing })));
const AppScreen = lazy(() =>
  import("./routes/AppScreen").then((m) => ({ default: m.AppScreen })),
);
const Review = lazy(() => import("./routes/Review").then((m) => ({ default: m.Review })));
const AuthScreen = lazy(() =>
  import("./routes/AuthScreen").then((m) => ({ default: m.AuthScreen })),
);
const Onboarding = lazy(() =>
  import("./routes/Onboarding").then((m) => ({ default: m.Onboarding })),
);
const ProfileSettings = lazy(() =>
  import("./routes/ProfileSettings").then((m) => ({ default: m.ProfileSettings })),
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
        <Route path="/app/review" element={<Review />} />
        <Route path="/app/profile" element={<ProfileSettings />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/login" element={<AuthScreen mode="login" />} />
        <Route path="/signup" element={<AuthScreen mode="signup" />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
