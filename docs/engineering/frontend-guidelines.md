# Frontend Engineering Guidelines

> **Status: LOCKED for Phase 6 (M4–M8).** This is the *engineering* contract for `web/`.
> [DESIGN.md](../../DESIGN.md) is the *visual* contract — it says what the product looks like and
> how it behaves; this document says how the code that produces it is written.
>
> Read both before writing a component. Where they overlap, DESIGN.md wins on appearance and this
> document wins on structure.

---

## 1. Why this document exists

The design system can be violated two ways. The obvious one is visual: wrong color, wrong radius,
wrong animation. Review catches those.

The dangerous one is structural. A component that derives a memory ID from narration text, or
fetches one memory per chip, or drops a loading state, looks correct in a screenshot. It passes a
design review. It is still wrong, and it breaks the thing the product is *for*.

This document is about that second category.

## 2. Layout

```
web/
  src/
    api/            the ONLY place fetch() is called
      client.ts     the fetch boundary; every response Zod-parsed here
      schemas.ts    runtime contracts mirroring api/routers/glassbox.py
      queries.ts    TanStack Query hooks (M4+)
    components/
      ui/           Base UI / shadcn primitives, re-themed to our tokens
      glassbox/     evidence chips, evidence rows, trace panel, receipts
      chart/        hand-rolled SVG: density bars, series line
      layout/       top bar, panes, drawer, composer shell
    routes/         one file per route: landing, today, app, profile, login, signup
      today/        Today's own components — single-use, so they live beside the route
                    (see the promote-on-second-use rule below), not in components/
    hooks/          shared behavior (useReducedMotion wrappers, media queries)
    lib/            pure helpers, no React
    styles/
      theme.css     THE token layer — this file is DESIGN.md §5, executable
      fonts.css     @font-face declarations
    assets/fonts/   self-hosted WOFF2 (no CDN)
  e2e/              @playwright/test specs — the 4 Definition-of-Done paths
```

**Rules**

- Nothing outside `src/api/` may call `fetch`.
- Nothing outside `src/styles/` may contain a color literal.
- `src/lib/` is React-free. If a helper needs a hook, it belongs in `src/hooks/`.
- A component that is used once lives beside its route, not in `components/`. Promote on the
  second use, not in anticipation of one.

## 3. The token layer

`theme.css` is the single source of truth for every visual value. Tailwind v4 generates the
utilities from it, so there is no second copy to drift.

```tsx
// ✅ token utilities
<div className="rounded-md border border-border bg-surface p-4">

// ❌ arbitrary values — the token is missing; add it to theme.css instead
<div className="rounded-[7px] border-[#2C313A] bg-[#14171C] p-[15px]">
```

**Arbitrary-value syntax (`[...]`) is banned for color, radius, font-size, and shadow.** It is
permitted for one-off layout dimensions that are genuinely not systematic (`max-w-[560px]`,
`w-[420px]` for the fixed engine pane), because inventing a spacing token for a single layout
constant is worse than the literal.

Token names follow the shadcn idiom (`background` / `foreground` / `border`) so shadcn components
paste in unmodified.

## 4. Two typefaces, and what they mean

This is the most-broken rule in the contract, because breaking it looks like styling.

```tsx
// ✅ mono because the value came from the database
<span className="font-mono text-meta">{memory.id}</span>
<span className="font-mono text-meta">conf {memory.confidence.toFixed(2)}</span>

// ❌ mono because it looks technical. This is a lie about provenance.
<h2 className="font-mono text-h2">Memory Engine</h2>
```

Ask before applying `font-mono`: *did this exact string come out of CockroachDB?* If it was
written by a designer, a developer, or the model, it is sans.

## 5. Data

### The boundary

Every response is parsed in `api/client.ts`. A component never sees an unvalidated payload, and
never parses one itself.

```tsx
// ✅
const { data, isPending, isError } = useQuery({
  queryKey: ["trace", turnId],
  queryFn: () => getTrace(turnId),
});

// ❌ raw fetch in a component: no validation, no cache, no shared loading state
const [trace, setTrace] = useState(null);
useEffect(() => { fetch(`/api/turns/${turnId}/trace`).then(r => r.json()).then(setTrace); }, []);
```

### Batching is not optional

```tsx
// ✅ one round trip for the whole evidence set (T16)
const { data } = useQuery({
  queryKey: ["memories", ids],
  queryFn: () => getMemoriesBatch(ids),
});

// ❌ N+1 across a cross-region link. This is the difference between a
//    responsive evidence pane and an unusable one.
ids.map(id => useQuery({ queryKey: ["memory", id], queryFn: () => getMemory(id) }));
```

### Query keys

`[resource, ...identifiers]`, resource first: `["trace", turnId]`, `["turns"]`, `["stats"]`,
`["memories", ids]`. Consistency here is what makes SSE invalidation a one-liner later.

### State decision tree

| Kind of state | Where it lives |
|---|---|
| Anything from the server | TanStack Query |
| Open/closed, hovered, focused, selected-chip | `useState` in the nearest common parent |
| Cross-cutting UI (drawer open on mobile) | One small context, created only when a second consumer appears |
| Derived from the above | Computed during render. Not `useEffect` + `useState`. |

There is no fourth category. If you are reaching for a store, re-read this table first.

## 6. Component authoring

Every component that renders server data ships **four** states. A component with only the
populated state is incomplete and does not merit review.

```tsx
export function EvidencePane({ turnId }: { turnId: string }) {
  const { data, isPending, isError, error } = useTrace(turnId);

  if (isPending) return <EvidenceSkeleton />;        // known shape → skeleton, never a spinner
  if (isError) return <PaneError error={error} />;   // names what failed; chat stays usable
  if (data.trace.evidence.length === 0) return <EvidenceEmpty />;  // honest, inviting
  return <EvidenceList evidence={data.trace.evidence} />;
}
```

**Props**

- Accept data, not fetchers. A presentational component takes `evidence`, not `turnId`.
- No `any`. No `as` casts to escape a type — if a cast seems necessary, the schema is wrong.
- Boolean props read positively: `isOpen`, not `isNotClosed`.
- No prop drilling past two levels; lift the fetch or add a context.

**Files**: one component per file, named export, file named after the component
(`EvidenceChip.tsx`). Default exports are reserved for route modules.

## 7. Motion

`LazyMotion` with `domAnimation` is mounted in `main.tsx` with `strict`, which means:

```tsx
import { m } from "motion/react";       // ✅ lazy features
import { motion } from "motion/react";  // ❌ throws under strict; bypasses the 5 KB budget
```

Every animated component checks reduced motion, and the reduced path must lose **no information**
— only movement:

```tsx
const reduce = useReducedMotion();

<m.div
  initial={reduce ? false : { opacity: 0, y: 4 }}
  animate={{ opacity: 1, y: 0 }}
  transition={{ duration: reduce ? 0 : 0.24, ease: [0.2, 0, 0, 1] }}
/>
```

Durations come from DESIGN.md §5.6 and nothing exceeds 400ms. If a component needs `useSpring`,
`useTransform`, or a variant tree, it is almost certainly over-animated — the product has exactly
one choreographed sequence (citation chip → evidence row).

## 8. Accessibility

Implementation obligations behind DESIGN.md §5.9:

- **Semantics before ARIA.** Evidence rows are `<ul><li>`. Stats are a `<dl>`. SQL is
  `<pre><code>`. A citation chip is a `<button>`, never a `<span>` with `onClick`. Reach for an
  ARIA attribute only when no element expresses the meaning.
- **Never remove a focus ring** without replacing it. `outline: none` alone fails the contract.
- **Icon-only buttons** carry an `aria-label`. An icon with no accessible name is invisible to a
  screen reader.
- **Charts** are `role="img"` with an `aria-label` summarizing the trend, plus a visually hidden
  `<table>` of the values. That table is how a screen reader reads a chart.
- **Live regions** are `polite`, never `assertive`. SSE insight arrivals are debounced so a burst
  announces once.
- **Touch targets** are ≥44×44px below 768px, including chips.

`@axe-core/playwright` runs against all four E2E paths. A new violation fails CI.

## 9. Base UI and shadcn

- **Base UI is the only primitive library.** Do not add Radix directly, `vaul`, `sonner`, or
  `cmdk` — each reintroduces a second portal/focus system, and that combination has documented
  bugs (Select inside Drawer breaking focus; a toast dismissal closing a drawer).
- shadcn components are **starting points we own**, pulled with `npx shadcn add` run *from
  `web/`*. After pulling one, replace its visual layer with our tokens before committing. A
  shadcn component committed with default styling is an incomplete change.
- Do not add a second component registry (Aceternity, Magic UI, React Bits, Origin UI). Rationale
  in DESIGN.md §11.

## 10. Performance

| Budget | Limit |
|---|---|
| Initial JS (gzip) | ≤ 150 KB |
| Fonts | ≤ 130 KB total, latin subsets only |
| Route chunks | Landing lazy-loaded; app shell eager |
| Interaction | No animation over 400ms; no layout shift on font load |

Measure with `chrome-devtools-mcp` (`performance_start_trace`, `lighthouse_audit`) rather than
guessing. When a budget is exceeded, find the cause before raising the number.

## 11. Testing

| Layer | Tool | Obligation |
|---|---|---|
| Types | `tsc -b` | Zero errors. Runs in CI on every push. |
| Contracts | Zod schemas | Every endpoint parsed at the boundary |
| E2E | `@playwright/test` | The 4 Definition-of-Done paths |
| Accessibility | `@axe-core/playwright` | Zero new violations |

The four required paths: signup → log → receipt → pane; money question → chips → trace;
slow-Bedrock UX; cross-user denial.

**The Playwright MCP server is not the test suite.** It is for driving a browser during
development. A test that exists only inside an agent session is not a test.

## 12. Review checklist

Enforceable form of DESIGN.md §12. A "no" blocks the merge.

- [ ] No hardcoded colors; no arbitrary values for color, radius, font-size, or shadow
- [ ] `font-mono` only on values that came out of the database
- [ ] No font size between 24px and 40px
- [ ] Empty, loading, error, and populated states all present
- [ ] Loading state matches its kind: skeleton for known shape, spinner only for indeterminate
- [ ] No structured value derived from narration text (ADR-12)
- [ ] Memory hydration batched, never per-chip (T16)
- [ ] `m.*` not `motion.*`; `useReducedMotion` honored; nothing over 400ms
- [ ] Interactive elements are real elements, keyboard-operable, with visible focus
- [ ] Icon-only controls have an accessible name
- [ ] Nothing distinguished by hue alone
- [ ] No new dependency without a DESIGN.md §11 entry

## 13. What is enforced where

| Rule | Enforced by |
|---|---|
| Type safety | `tsc -b`, CI `web` job |
| Response contracts | Zod, at runtime, on every fetch |
| Accessibility violations | `@axe-core/playwright`, CI |
| Bundle budget | Manual, via Lighthouse |
| Token discipline, mono-as-provenance, four states | **Review only** |

The last row is the honest gap. The three most important rules in this document are not
mechanically enforced, which is exactly why they are written down this precisely. If M4–M8
produces a linting rule for any of them, add it here.

## 14. Agent toolkit (Claude Code)

Locked 2026-08-07 alongside the design system. The enablement itself is committed
(`.claude/settings.json`, `.mcp.json`) so a fresh clone gets it automatically; this section is the
*reasoning*, which config files cannot carry.

| Tool | Scope | Why it is here |
|---|---|---|
| `context7` MCP | user | Current library docs. It corrected five stale version assumptions during F2 — treat it as the default over recall for any library question. |
| `shadcn` MCP | project (`.mcp.json`) | Browse and pull registry components. **Caveat below.** |
| `frontend-design` plugin | project | Anthropic's skill for distinctive UI; directly counters the generic-AI aesthetic this project is defined against. |
| `chrome-devtools-mcp` plugin | project | Performance traces, Lighthouse, console, a11y debugging. This is how §10's budget gets *measured* instead of estimated, and it verified the tokens rendering during F3. |
| `playwright` plugin | project | Agent-driven browsing during development. **Not the test suite** — see §11. |
| `modern-web-guidance` plugin | project | Catches obsolete CSS/JS patterns. Low cost, occasional real save. |

**shadcn MCP caveat.** It resolves `components.json` from the working directory, and that file
lives in `web/`, not the repo root. Registry *browsing* works from anywhere; component
*installation* must run as `npx shadcn add <name>` from inside `web/`. Running it from the root
either fails or scaffolds into the wrong place.

**Disable the Vercel plugins for this project.** They are installed at user scope and inject ~30
`vercel:*` skills. This project deploys to **ECS Express Mode**, not Vercel (ADR-13.3), so they are
pure noise — and `vercel:knowledge-update` claims a session-start slot for a platform we do not use.

**Do not add a second component registry.** Aceternity, Magic UI, React Bits, and Origin UI were
all evaluated and rejected; rationale in [DESIGN.md §11](../../DESIGN.md). Adding one reintroduces
the aesthetic the design system exists to avoid, and a second component idiom in one codebase.

## 15. Maintenance notes

- **Do not touch casually:** `theme.css` (it is the design system, not a stylesheet),
  `api/schemas.ts` (it mirrors a backend contract — change the backend first),
  `LazyMotion strict` in `main.tsx` (removing it silently triples the motion bundle).
- **Revisit when:** light mode enters scope (post-hackathon), routing grows past four routes
  (reconsider TanStack Router), or SSE proves unworkable through the ALB (fallback is Query's
  `refetchInterval`).
- **This document can be retired** when the frontend has a linting setup that enforces §12
  mechanically. Until then it is the contract.

## 16. Related files

| Path | What it is |
|---|---|
| [DESIGN.md](../../DESIGN.md) | The visual contract: tokens, components, landing spec, 20 rules |
| [web/src/styles/theme.css](../../web/src/styles/theme.css) | The token layer, executable |
| [web/src/api/schemas.ts](../../web/src/api/schemas.ts) | Runtime contracts for the glass-box API |
| [web/src/api/client.ts](../../web/src/api/client.ts) | The single fetch boundary |
| [api/spa.py](../../api/spa.py) | How the built bundle is served from the API container |
| [glass-box-architecture.md](glass-box-architecture.md) | The backend contract this UI renders |
| [07-glass-box-ui.md](../office-hours/07-glass-box-ui.md) | Approved visual grammar (wireframe v3) |
