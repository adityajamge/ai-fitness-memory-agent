/**
 * Foundation smoke screen — **scaffolding, not product**.
 *
 * Its only job is to prove the stack is wired: both typefaces resolve, the theme tokens are
 * generated, the router mounts, and the Query provider is live. M4 replaces this file entirely
 * with the real routes (`/`, `/app`, `/login`, `/signup`).
 *
 * Deliberately not a component showcase: those get built against DESIGN.md in M4, and a
 * throwaway showcase would only invite copy-paste of code written before the guidelines existed.
 */

import { Route, Routes } from "react-router";

function Foundation() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-[560px] flex-col justify-center gap-6 px-6">
      <div>
        <p className="font-mono text-micro uppercase text-faint">foundation</p>
        <h1 className="mt-2 text-h2 text-foreground">Fitness Memory Agent</h1>
      </div>

      <p className="text-body text-muted-foreground">
        The design system is wired. Product screens land in M4, built against{" "}
        <span className="font-mono text-meta text-foreground">DESIGN.md</span>.
      </p>

      {/* Two voices, visible: sans is the model talking, mono is the database talking. If these
          render in the same face, the font pipeline is broken and the glass box loses its
          primary signal (DESIGN.md §4.1). */}
      <dl className="rounded-md border border-border bg-surface p-4">
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-dense text-muted-foreground">sans — a model wrote this</dt>
          <dd className="text-dense text-foreground">Satoshi</dd>
        </div>
        <div className="mt-3 flex items-baseline justify-between gap-4 border-t border-border pt-3">
          <dt className="text-dense text-muted-foreground">mono — the database said this</dt>
          <dd className="font-mono text-meta text-foreground">IBM Plex Mono · conf 0.90</dd>
        </div>
      </dl>

      <p className="text-meta text-faint">
        <span className="mr-1.5 text-signal">✦</span>
        signal is reserved for evidence you can open
      </p>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="*" element={<Foundation />} />
    </Routes>
  );
}
