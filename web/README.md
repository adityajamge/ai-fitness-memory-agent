# web/ — Glass-Box UI

Vite + React + TypeScript SPA, built to static assets and served by FastAPI from the same
container (ADR-13.7). Scaffolded in Phase 6 / F0–F5 (commit `fa2dcd5`); product screens land in M4.

**Current status, verification evidence, and open risks: [DESIGN.md §0](../DESIGN.md#0-frontend-foundation-status).**
**What to build first: [DESIGN.md §15](../DESIGN.md#15-m4-build-order)** (seven tasks, F-T1…F-T7).

## Run it

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api → http://127.0.0.1:8080
```

Run `uvicorn api.main:app --port 8080` from the repo root alongside it for a working API. In
production both are one origin inside one container, so relative `/api` paths and
`credentials: "same-origin"` behave identically in dev and prod.

```bash
npm run build      # → dist/, picked up automatically by api/spa.py
npm run typecheck
npm run test:e2e
```

## Before you write a component

Two documents govern this directory, and they are not optional reading:

| Document | Owns |
|---|---|
| [DESIGN.md](../DESIGN.md) | Appearance and behavior: tokens, type scale, component language, landing spec, the 20 design rules |
| [engineering/frontend-guidelines.md](../docs/engineering/frontend-guidelines.md) | Code structure: layout, the fetch boundary, state, motion, accessibility, the review checklist |

Visual grammar (approved wireframe v3) is in
[office-hours/07-glass-box-ui.md](../docs/office-hours/07-glass-box-ui.md); the backend contract
this UI renders is [engineering/glass-box-architecture.md](../docs/engineering/glass-box-architecture.md).

## Three things that surprise people

**`src/styles/theme.css` is the design system, not a stylesheet.** Tailwind v4 generates every
utility from the custom properties in it, so a color literal anywhere else means a token is
missing.

**Sans and mono mean different things.** Satoshi = a model wrote this. IBM Plex Mono = the
database produced this. Using mono because something "looks technical" is a lie about provenance
and breaks the glass box's primary signal.

**Fonts are vendored on purpose.** No font CDN: one image, one origin, no third-party
availability dependency. See [src/assets/fonts/README.md](src/assets/fonts/README.md).
