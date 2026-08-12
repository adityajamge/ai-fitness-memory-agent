# Design System — AI Fitness Memory Agent

> **This file is the visual contract for Phase 6 (M4–M8).** Every React component must obey it.
> Read it before any UI decision. Deviations require explicit approval and a Decisions Log entry.
>
> Companions: [docs/office-hours/07-glass-box-ui.md](docs/office-hours/07-glass-box-ui.md) (visual
> *grammar*, approved wireframe v3) · [docs/engineering/glass-box-architecture.md](docs/engineering/glass-box-architecture.md)
> (the locked backend contract this UI renders) · [docs/office-hours/09-decisions.md](docs/office-hours/09-decisions.md) (ADRs).
> Where 07 says *what goes where*, this file says *what it looks like and how it behaves*.

---

## 0. Frontend Foundation Status

**As of 2026-08-08. M4–M8 build order complete (07's eight items, all shipped or deliberately cut
per §13). Remaining work is hardening, not new UI surface — see the end of this section.**

| Step | Delivered |
|---|---|
| **F0** | Agent toolkit installed and locked (§11, and [frontend-guidelines.md §16](docs/engineering/frontend-guidelines.md)) |
| **F1** | This document — design system approved as the M4–M8 visual contract |
| **F2** | `web/` scaffolded on the approved stack; tokens live and verified in-browser |
| **F3** | Wired into Docker (Node 24 stage), CI (`web` job), and FastAPI ([api/spa.py](api/spa.py), 12 tests) |
| **F4** | [frontend-guidelines.md](docs/engineering/frontend-guidelines.md) — the engineering contract |
| **F5** | `/plan-design-review`: 7/10 → 9/10, six decisions applied (§16), seven tasks queued (§15) |
| **M4** | SPA foundation → chat shell: F-T1…F-T4, landing, auth, app shell, engine pane, primitives |
| **M5** | Citation chips, batch hydration, the chip→row choreography, query display, mobile drawer |
| **M6** | Live engine pane: SSE stage narration with an automatic plain-transport fallback |
| **M7** | Timeline strip: density bars, changepoint caps, mobile weekly bucketing, click-to-scrub |
| **M8** | Insight lineage: the text list (§13's designated shipped form; the graph stays cut) |
| **Today** | `/app/today` (§6.20) — the home briefing. First surface added *after* the M4–M8 build order, from the 2026-08-12 competitive research (P0 #1) |

**Today shipped 2026-08-12** (commit `8aed61a`), the first new product surface since M8 and the
first driven by competitive research rather than the original build order. It answers "how am I
doing?" without requiring a question first — the gap the research named as AyuMind's largest, on
the grounds that all five products studied open to a Today-style home while AyuMind opened to a
composer.

Backed by one new endpoint, `GET /api/today` (`engine/today.py` + `api/routers/today.py`):
**deterministic, no model call**, composing capabilities that already existed — `fetch_stats`,
`get_profile` + `compute_targets`, `aggregate_memories` over `protein_g`/`kcal` for today and
yesterday, `latest_weight`, and day-grouped coverage — in **one** round trip, because six over the
`us-east-1` → `ap-south-1` hop is the N+1 mistake at page level. It returns the `RetrievalStep`s
it ran, which is what lets the screen carry the same glass box a conversational turn does.

The frontend reuses `Composer`, `Timeline`, `TopBar`, `EmptyState`, `ErrorState`, `Skeleton`,
`ConfidenceMeter` and `RetrievalQueries` unmodified; the only new components are the four in
`web/src/routes/today/`. Today has **no send path and no day view of its own** (§6.20).

Two pre-existing defects surfaced and were fixed in the same commit. The larger one:
`Timeline.tsx`'s accessible data table carried `sr-only` on the `<table>` itself, and that utility
cannot shrink a table (tables size to content regardless of a 1×1 rule). A 114-day account
produced a clipped, absolutely-positioned **2,784px** box that pushed `document.scrollHeight` to
nearly three screens of empty scroll. It had been invisible for as long as `AppScreen` — whose
shell is `h-dvh overflow-hidden` — was the only consumer, and appeared the moment a scrolling
screen rendered the same component. Now on a wrapper `div`. The smaller one: three E501s in
`api/routers/profile.py` left by the ADR-17 commit.

Verified: 6 new engine tests green against real CockroachDB (they pin the null-vs-zero contract in
both directions, the local-midnight day split, coverage semantics, and the recent-strip
exclusions) · 762 Python tests passed · ruff and `tsc` clean · **15/15 Playwright green**,
including both axe assertions and the mobile-timeline test that covers the `sr-only` change ·
initial bundle **unchanged at 108.06 KB gzip** (Today is 3.65 KB, lazy) · smoke-tested at 1440×900
and 390×844, light and dark, against both the real replayed history and a seeded account: no
console errors, no horizontal overflow, composer above the fold at 390px.

**Foundation commit: `fa2dcd5`** — `feat(web): frontend foundation — design system, scaffold, and serving`.
Verified at that commit: 766 Python tests passed · ruff clean · `tsc -b` clean · production build
320 KB / 103 KB gzip · tokens confirmed rendering in a real browser.

**Not verified there:** the Docker image never built locally (no daemon on the dev machine); CI is
the first real check. No visual mockups exist — the gstack designer needs an `OPENAI_API_KEY` that
was not set.

**M6 shipped:** `POST /api/chat/stream` — the SSE twin of `/api/chat`, narrating `retrieving` /
`assembling context` / `generating` (or `extracting` for an ingest turn) as the graph's nodes
actually complete (`agent/graph.py`'s `run_turn_stream`, driven by `graph.stream(stream_mode=
"updates")`, never a timer). The frontend (`web/src/api/chatStream.ts`) hand-rolls SSE-over-`fetch`
(`EventSource` cannot send a POST body) and **resolves §11's open risk at runtime instead of by
static choice**: any connection that fails to establish as an event stream, or closes without a
`done`/`error` frame, throws `StreamUnavailableError` and `AppScreen` falls back to the
already-tested plain mutation for that turn — invisibly, mid-conversation, with no user-visible
difference beyond the progress line not appearing. A frame the *graph itself* produced (an
`error` event, e.g. a Bedrock failure) is not that signal and is reported as a real turn failure
on both transports identically. Verified: a raw SSE curl against the real dev stack showed all
three/two-stage sequences correctly; **14/14 Playwright E2E green**, and the dev API's access log
confirms all five chat turns in that run went through `/api/chat/stream` with zero fallbacks.
**Not verified: the ALB hop in the actual deployed container** — AWS access is still blocked (see
`TODOS.md`), so the runtime fallback is what makes that gap safe to ship past rather than a
blocker, per the milestone brief's own guidance ("if it does not [work], choose the smallest,
clean fallback... without redesigning").

**M7 + M8 shipped together** (both frontend-only; the backend groundwork — `GET /api/timeline`,
retrieval-insight fields — already existed): the timeline strip (`web/src/components/timeline/
Timeline.tsx`) — hand-rolled SVG density bars, a 2px `--signal` cap on changepoint days (the one
other permitted use of the signal token, rule 7), a mono hover tooltip, click-to-scrub that
scrolls the matching turn into view with a `--surface-2` highlight (never `--signal` — rule 7
again), and the fourth designed empty state ("your memory starts here"). Below 768px it buckets
into 7-day chunks at a fixed 16px each inside a horizontally scrolling rail, with the `now`
marker and tooltip pinned to the non-scrolling viewport — §5.8's explicit fix for a 300-day
account rendering unreadable 1px bars on a 390px phone (F-T7, previously unchecked).

Insight lineage cards in the evidence pane are now interactive: clicking one expands to
`pattern_strength` (via the same `ConfidenceMeter` evidence rows use — same 0–1 scale, one visual
language, not two) and the retraction condition rendered as prose (`engine.insights.
render_retraction_condition`, never a raw structured condition — rule 16). Both fields are new,
additive `EvidenceTrace.insights[]` keys; the Zod schema defaults them for traces persisted
before this change, since the trace is served verbatim (I-29) and old rows simply lack the keys.
The reasoning-lineage **graph** stays cut per §13 — this text list is its designated shipped
form, not a placeholder for it.

Also landed: the `E`/`T` keyboard shortcuts and `Esc`-blurs-composer (§9's shortcut table, minus
the cut command palette — §13), and F-T6's safe, verifiable subset (§15).

Verified: 772 Python tests green (2 new, locking `pattern_strength`/`retraction` end to end from
a seeded `retraction_condition` through `assemble()`); **15/15 Playwright E2E green**, including a
dedicated 390×844 mobile-viewport run of the bucketed timeline with zero axe violations; `tsc` and
`ruff` clean; initial bundle unchanged at 106.56 KB gzip.

**M5 shipped:** citation chips, batch-hydrated evidence, the chip→row choreography, the executed
query display, the mobile evidence drawer, and per-turn trace fetching so history stays
inspectable. **14/14 Playwright E2E green** (paths 1 and 2) including axe. Two real defects found
by measuring: a history-seed race that erased in-flight turns, and `--faint` failing AA on raised
surfaces across 143 nodes.

**M4 shipped:** all four P1 tasks (F-T1…F-T4), the landing page, auth, the app
shell, the engine pane with form-encoded provenance/confidence, and the design-system primitives.
Verified against a real API and a real CockroachDB: **8/8 Playwright E2E green including an axe
assertion**, zero WCAG 2.2 AA violations across all four routes, initial bundle **106 KB gzip**
(budget 150).

**Deferred from M4 by design, now resolved:** the mobile evidence drawer (§5.8) landed with the
full evidence pane in M5, ahead of the M6 slot originally planned for it.

---

## 1. Product Philosophy

### What this is

A personal health companion whose product **is** its memory. The user talks; every message becomes
a typed, queryable, evidence-grade row in CockroachDB. Months later the agent reasons across the
whole history and answers with dated, cited proof.

### Who it is for

Two audiences, in this order, for the duration of the hackathon:

1. **Hackathon judges** (technical, skeptical, time-boxed to minutes). They are scoring *Agentic
   Memory Design*. They need to see evidence, not claims.
2. **The person whose body this is.** The first-person demo narrative ("my real year, my real
   body-fat drop") only lands if the interface treats health data with seriousness.

Designing for (1) does not mean building a developer tool. It means **refusing to hide the
machinery**, which happens to be what (2) deserves too.

### The one thing to remember

> **It remembers. And it can prove it.**

Every design decision in this document serves that sentence. A choice that makes the product
prettier but the *proof* less visible is the wrong choice.

### The material

The material of this product is not "fitness" and not "AI". It is **evidence**: a clickable receipt
behind every sentence. Fitness apps are made of rings, streaks, and green checkmarks. AI products in
2026 are made of purple gradients and sparkles. This product is made of rows, IDs, timestamps,
confidence values, and the actual SQL that ran.

### Three things we refuse to do

1. **Never fake certainty.** Confidence scores, `reconstructed` provenance, and retracted insights
   are shown, not hidden. ADR-13.12 forbids probability language; the UI says "pattern strength"
   and speaks in hypotheses.
2. **Never render model output as structured data.** ADR-12 is absolute: everything structured on
   screen comes from the deterministic `EvidenceTrace` and the engine APIs. Prose comes from the
   model. Nothing else does.
3. **Never make the empty state look broken.** Every account starts empty (ADR-13.4), including
   every judge's. A brand-new account is a *first chapter*, not an error.

---

## 2. Brand Identity

### Name and mark

**AyuMind AI.** The mark (replaced 2026-08-09, explicit instruction — see §16 Decisions Log) is
`Logo.tsx`, used everywhere: two counter-rotating rings around an ECG trace that flows along its
own path, with a pulsing center node and a slow heartbeat scale on the whole mark. It carries
hardcoded gradients and a glow filter — a deliberate, scoped exception to rules 6 and 12, not an
oversight. The original mark (a filled square with a hairline inset square, `--signal` on
`--background`, no gradient) is retired along with the `Mark` component that rendered it.

### Voice

Precise, quiet, and unafraid of numbers. The product speaks like a good lab report, not like a
coach and not like a chatbot.

| Say | Not |
|---|---|
| "Protein rose ~96→142 g/day from May 12." | "Great job crushing your protein goals! 💪" |
| "Pattern strength: moderate. 26 memories support this." | "There's an 82% chance that…" |
| "Saved. Parsing incomplete, kept as a note." | "Oops! Something went wrong." |
| "Your memory starts here." | "No data yet." |

### Aesthetic direction

**Instrument.** Industrial/utilitarian at the core, brutally minimal at the marketing surface.
Decoration level: **minimal**, with exactly one intentional texture (the graph rule, §4.4).

Reference points measured on 2026-08-07: [linear.app](https://linear.app) (dark ramp discipline,
mono-as-machine-voice), [vercel.com](https://vercel.com) (type-scale gap, restraint),
[stripe.com](https://stripe.com) (light display weights), [supabase.com](https://supabase.com)
(oklch color pipeline). We take the *discipline* from these and none of the palettes.

---

## 3. Visual Principles

Seven rules, ordered. When two conflict, the earlier wins.

1. **Proof outranks polish.** If an animation, a spacing choice, or a color delays or obscures the
   path from claim → evidence, it loses.
2. **Two voices, always.** Sans is the model talking. Mono is the database talking. This is
   semantic, not stylistic (§4.2). It is the single most important rule in this document.
3. **Form carries meaning before color does.** Provenance, confidence, and status are encoded as
   shape, fill, and stroke first. Color is a reinforcement, never the sole channel (WCAG 1.4.1).
4. **Colour is rare and it means one thing.** `--signal` means *this is evidence you can open*. It
   appears nowhere else. Scarcity is what gives it force.
5. **Restraint over contrast.** Surfaces separate by 3–8 points of lightness and a hairline, not by
   heavy borders or shadows. Loud separation reads cheap.
6. **Density is a feature, not a failure.** This is an instrument. Tight, aligned, scannable data is
   the point. Do not pad the evidence pane to make it feel "airy".
7. **Honest empty, honest slow, honest broken.** Empty, loading, and error states are designed
   first-class, never as afterthoughts. They are three of the ten Phase-6 deliverables.

### Anti-patterns (automatic rejection in review)

- Purple or violet gradients, anywhere.
- Three-column feature grid with icons in colored circles.
- Gradient buttons, glow beams, spotlight hovers, 3D tilt cards, animated aurora backgrounds.
- Uniform large border-radius on everything.
- `system-ui` / `-apple-system` as a display or body face.
- Centered-everything layouts with uniform vertical rhythm.
- Emoji as UI iconography.
- Green "success" checkmarks as the primary feedback for logging a memory.
- Any chart whose colors were chosen by a library default.

---

## 4. Design Language

### 4.1 The core idea: typeface as provenance

There are exactly **two** typefaces on screen, and the choice between them is a claim about where
the content came from.

| Face | Means | Used for |
|---|---|---|
| **Satoshi** (sans) | A model wrote this | Narration, headings, labels, buttons, marketing copy |
| **IBM Plex Mono** | The database produced this | Memory IDs, timestamps, SQL, confidence values, provenance tags, metrics, counts, aggregate results, trace fields |

A user learns this in ten seconds and never unlearns it. It renders ADR-12 as visual grammar: you
can *see* which parts of an answer are generated prose and which parts are machine fact, without a
legend and without reading a word.

**This rule is absolute.** Mono is never used for emphasis, style, or "developer vibes". A heading
set in mono is a bug. See §13, Rule 2.

### 4.2 Number discipline

All numerals in a data context use `font-variant-numeric: tabular-nums`. Columns of figures must
align on the decimal. IBM Plex Mono is tabular by construction; Satoshi requires the explicit
declaration wherever it renders a figure (rare, and generally a mistake per §4.1).

### 4.3 Surface model

Elevation on near-black **does not come from shadows**. Drop shadows on a `#0B0D10` page are
invisible or muddy. Elevation is expressed as **surface lightness + a hairline border**:

```
depth 0   --color-background   page
depth 1   --color-surface      panes, cards
depth 2   --color-surface-2    rows inside a pane, hover states
depth 3   --color-surface-3    overlays only (dialog, popover, drawer)
```

Shadows are reserved **exclusively** for depth 3, where they signal detachment from the page
plane. Everywhere else, a 1px `--color-border` border does the work. See §5.5.

### 4.4 The graph rule (the one texture)

A 1px hairline grid at 4% opacity, 24px pitch, drawn behind the timeline strip and the landing
hero. It evokes chart paper and a measurement axis. It is the only decorative element in the
system, and it is semantic: this product plots things.

It never appears behind body copy, never animates, and never exceeds 4% opacity.

---

## 5. The Design System

### 5.1 Typography

**Families**

| Role | Family | Weights | Delivery |
|---|---|---|---|
| Display / Body / UI | **Satoshi** | 400, 500, 700 (variable) | Self-hosted WOFF2, `web/src/assets/fonts/` |
| Engine output | **IBM Plex Mono** | 400, 500 | `@fontsource/ibm-plex-mono`, self-hosted |

Both are self-hosted. **No font CDN.** The app ships as one Docker image behind ECS Express Mode
(ADR-13.3/13.7); a third-party font request is an availability dependency we do not accept and a
render-blocking round trip we do not need. `font-display: swap`, both preloaded in `<head>`.

Fallback stacks:
```css
--font-sans: "Satoshi", ui-sans-serif, system-ui, sans-serif;
--font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
```

**Scale** — note the deliberate hole between 24 and 40. Measured on both Linear and Vercel; the gap
*is* the hierarchy. Never introduce a size inside it.

| Token | px | rem | Face | Tracking | Leading | Use |
|---|---|---|---|---|---|---|
| `text-micro` | 11 | 0.6875 | mono, uppercase | +0.08em | 1.4 | Badge labels: `LIVE`, `CONF`, `RECONSTRUCTED` |
| `text-meta` | 12 | 0.75 | mono | 0 | 1.5 | IDs, timestamps, trace fields |
| `text-dense` | 13 | 0.8125 | sans | 0 | 1.5 | Evidence rows, table cells, dense UI |
| `text-body` | 15 | 0.9375 | sans | 0 | 1.6 (24px) | Chat, body copy, inputs |
| `text-lead` | 17 | 1.0625 | sans | −0.005em | 1.6 | Lead paragraphs, sub-heads |
| `text-h3` | 20 | 1.25 | sans, 500 | −0.01em | 1.4 | Pane titles |
| `text-h2` | 24 | 1.5 | sans, 500 | −0.015em | 1.3 | Section headings |
| — | | | | | | **← no sizes live here** |
| `text-display-s` | 40 | 2.5 | sans, 500 | −0.025em | 1.1 | Landing section display |
| `text-display-m` | 56 | 3.5 | sans, 500 | −0.03em | 1.05 | Landing secondary hero |
| `text-display-l` | 72 | 4.5 | sans, 500 | −0.035em | 1.0 | Landing hero only |

Body copy is **15px, not 16px**. Measured on Linear; at this density 15/24 is the correct rhythm
and 16 feels loose.

Display weight is **500, never 700**. Large type at heavy weight reads as shouting; the reference
set runs 300–510 at display sizes. Bold (700) exists only for inline emphasis in body copy.

### 5.2 Color

Declared in **oklch** (Tailwind v4 is oklch-native and it keeps perceptual lightness steps even).
Hex equivalents given for reference.

Token names follow the **shadcn/Tailwind idiom** (`background` / `foreground` / `border`) so
shadcn components paste in and work unmodified. The obvious alternative (`--color-text`) generates
`text-text-muted`, which reads badly in every className and would need rewriting on every paste.

The live source of truth is [`web/src/styles/theme.css`](web/src/styles/theme.css) — this block
is a copy for reading, and the CSS file wins if they ever disagree.

```css
@theme {
  /* Surfaces — cold, blue-shifted near-black. Ramp spans ~12 points of lightness. */
  --color-background:     oklch(0.145 0.008 250);  /* #0B0D10  page */
  --color-surface:        oklch(0.185 0.010 250);  /* #14171C  panes, cards */
  --color-surface-2:      oklch(0.225 0.012 250);  /* #1D2128  rows, hover */
  --color-surface-3:      oklch(0.265 0.012 250);  /* #262B33  overlays */
  --color-border:         oklch(0.310 0.012 250);  /* #2C313A  hairlines */
  --color-border-strong:  oklch(0.400 0.014 250);  /* #3D444F  focused borders */

  /* Foreground */
  --color-foreground:       oklch(0.965 0.003 250);  /* #F4F6F8  17.4:1 */
  --color-muted-foreground: oklch(0.720 0.012 250);  /* #A2A9B4   8.3:1 */
  --color-faint:            oklch(0.640 0.014 250);  /* #868D95 */

  /* Signal — the only accent. Means: this is evidence you can open. */
  --color-signal:        oklch(0.800 0.155 78);   /* #FFB224  11.0:1 */
  --color-signal-dim:    oklch(0.800 0.155 78 / 0.14);  /* chip fill */
  --color-signal-active: oklch(0.800 0.155 78 / 0.28);  /* chip selected */

  /* Invalid — retracted insights, invalid citations, destructive actions. */
  --color-invalid:     oklch(0.650 0.190 25);   /* #E5484D   5.4:1 */
  --color-invalid-dim: oklch(0.650 0.190 25 / 0.14);
}
```

**Contrast is measured against the worst surface a token can land on**, not against the page.
That distinction is not pedantry: `--color-faint` was originally `oklch(0.590)` and measured a
comfortable 4.82:1 on `--background` — while failing at **4.16:1 on `--surface-2`** and **3.72:1
on `--surface-3`**, which is where most faint text actually sits. Axe caught it across 143 nodes
during M5. Checking a foreground against one background is not checking it.

| token | background | surface | surface-2 | surface-3 |
|---|---|---|---|---|
| `--foreground` | 17.4 | 16.4 | 15.0 | 13.4 |
| `--muted-foreground` | 7.99 | 7.52 | 6.90 | 6.17 |
| `--faint` | 5.89 | 5.55 | 5.09 | **4.55** |

`--faint` is the floor; nothing lighter than it may carry text, and 4.55:1 on `--surface-3` is
the number that constrains it.

**The semantic budget is two colors.** `--signal` (evidence) and `--invalid` (retracted/invalid).
That is the entire meaningful palette.

- There is **no success green.** A created memory is confirmed by the `✦` receipt mark and mono
  detail, not by color. Green checkmarks are the fitness-app cliché this product is defined against.
- There is **no warning amber**, because amber is spent on `--signal`. Warnings are carried by icon
  plus text. This is a deliberate trade (§3, Risk 2).
- Provenance and confidence use **no color at all** (§5.9).

**Theme scope.** Both dark and light are supported (added 2026-08-09 — see §16 for how the
original "dark-only, tokens light-ready" scope became additive rather than deferred). Every color
is a CSS variable, resolved through `[data-theme="dark"|"light"]` in `theme.css` — do not hardcode
a hex anywhere; that rule never changed, only which themes exist to serve. **The landing page
(§8) is the one exception, pinned dark regardless of the toggle** (§16, same day) — everything
past `/signup`/`/login` still adapts.

### 5.3 Spacing

Base unit **4px**. Density is **compact in the app**, **spacious on marketing**.

```
space-0.5   2px    icon-to-label, badge internal
space-1     4px    tight stack
space-2     8px    control padding-y, chip gap
space-3    12px    row padding, list gap
space-4    16px    card padding, pane gutter
space-6    24px    section gap in app
space-8    32px    pane padding on desktop
space-12   48px    marketing block gap (mobile)
space-16   64px    marketing block gap (tablet)
space-24   96px    marketing section gap (desktop)
space-32  128px    hero vertical breathing
```

App surfaces never exceed `space-8` for internal padding. Marketing surfaces never go below
`space-12` between blocks.

### 5.4 Border radius

Small radii are a deliberate departure from the 12–16px category norm. Calipers, not marshmallows.

```
radius-xs    2px   badges, chips, provenance tags
radius-sm    4px   buttons, inputs, selects
radius-md    6px   cards, evidence rows
radius-lg    8px   panes, dialogs, drawers
radius-full  9999  avatar ONLY
```

Nothing else is ever a pill. A pill-shaped button is a bug.

### 5.5 Shadows and elevation

Shadows exist **only** at depth 3 (overlays). Elevation everywhere else is surface + hairline (§4.3).

```css
--shadow-overlay: 0 16px 48px -12px oklch(0 0 0 / 0.7),
                  0 0 0 1px var(--color-border);
--shadow-popover: 0 8px 24px -8px oklch(0 0 0 / 0.6),
                  0 0 0 1px var(--color-border);
```

There is no `shadow-sm`, no `shadow-md`, no hover-lift shadow. A card that lifts on hover is a bug;
cards change `--surface` → `--surface-2` instead.

### 5.6 Motion

**Approach:** minimal-functional, with exactly one choreographed moment.

**Durations**
```
duration-instant   0ms     state that must feel direct (checkbox, tab)
duration-micro   120ms     hover, focus, color change
duration-short   180ms     chip activation, row highlight
duration-medium  240ms     panel expand, tooltip, popover
duration-enter   320ms     dialog, drawer, first paint of a list
duration-slow    400ms     the citation→evidence choreography (ceiling)
```

**Nothing exceeds 400ms.** The one exception is the timeline scrub, which is user-driven and
therefore has no duration at all.

**Easing**
```css
--ease-out:  cubic-bezier(0.2, 0, 0, 1);      /* entering, expanding */
--ease-in:   cubic-bezier(0.4, 0, 1, 1);      /* leaving, collapsing */
--ease-move: cubic-bezier(0.4, 0, 0.2, 1);    /* position changes */
```

**The signature motion.** Clicking a citation chip does three things in one 400ms sequence: the
chip fills with `--signal-dim`, the matching evidence row in the engine pane highlights, and the
pane scrolls that row into view. Claim and proof are physically linked. This is the only
choreographed animation in the product, and it is worth the budget because it *is* the product.

**Reduced motion is not optional.** A glass box that slides, pulses, and streams is hostile to
users with vestibular sensitivity. Every animated component reads `prefers-reduced-motion`. Under
reduce, transforms and opacity transitions become instant state changes; the citation choreography
becomes an instant highlight plus `scrollIntoView({ behavior: 'auto' })`. Nothing is lost, nothing
moves.

### 5.7 Grid and layout

**App (wireframe v5 — thread sidebar moved under the header/timeline 2026-08-09, §16), desktop
≥1280px:**

```
┌───────────────────────────────────────────────────────────┐
│ top bar                          56px   mark · stats · acct│
├───────────────────────────────────────────────────────────┤
│ timeline strip                   72px   full width          │
├────────────┬────────────────────────────────┬──────────────┤
│  sidebar   │ conversation                   │ memory engine│
│  272px     │ minmax(560px, 1fr)             │ 420px fixed  │
│  fixed     │                                │              │
│            ├────────────────────────────────┤              │
│            │ composer           auto        │              │
└────────────┴────────────────────────────────┴──────────────┘
```

The sidebar is a **peer of the conversation and the engine pane, below the top bar and timeline**
— not the ChatGPT-style full-viewport-height column wireframe v4 specified. It is fixed at
**272px** and collapsible (a handle on its own border, same mechanism as the pane's), never a
drawer on desktop.

The engine pane is **fixed at 420px** and does not flex. Evidence rows have a natural width; letting
the pane grow on a wide monitor produces stretched, unreadable rows.

**User-collapsible, added 2026-08-09** (§16 Decisions Log) — not a contradiction of "does not
flex": collapsing removes the pane, it does not let anything else grow to claim the freed 420px.
A handle centered on the border between the two columns toggles it; nothing else about the pane's
width (open, it is still exactly 420px) or the conversation's own cap changes.

**The conversation is capped too, and this is not the same as the pane being fixed.** Turn content
has `max-width: 72ch` (≈640px at 15px) and centers within its column; extra width past that becomes
margin, not measure. Comfortable reading measure is 60–75 characters, and an uncapped column on a
2560px monitor produces ~2000px lines of 15px text that nobody can track a line-break in. The
column still grows — the *text inside it* does not.

This matters more here than in a normal chat app: the reviewer's monitor is the widest screen this
product will ever be judged on.

**Marketing:** max content width **1120px**, 12-column grid, 24px gutters. Hero and the money-shot
section may break the grid to full-bleed; nothing else may.

### 5.8 Responsive breakpoints

Tailwind defaults are kept as tokens, but only three transitions are *designed*:

| Range | Name | Layout |
|---|---|---|
| < 768px | **mobile** | Single column. Conversation full-width. Engine pane becomes a **right-side drawer** (amended 2026-08-09 from an original bottom sheet — §16) opened by tapping a citation chip or the pane handle; the thread sidebar (added 2026-08-09, §16) becomes a **left-side drawer** the same way, opened by its own handle. Timeline collapses to a horizontally scrollable rail. Top-bar stats collapse to a single count with a tap-to-expand sheet. |
| 768–1279px | **tablet** | Two columns. Conversation + engine pane, but the pane is **collapsible** and defaults closed in portrait, open in landscape. The sidebar is a drawer at this width too, same as mobile — three simultaneous fixed-width columns (272 + conversation + 420) is a desktop-only claim. Timeline full width, reduced to 48px. |
| ≥ 1280px | **desktop** | Full four-region layout above (sidebar, top bar/timeline/conversation, engine pane). |

**The mobile decision is load-bearing and was made now, not later.** There is no honest way to show
three panes on a 390px screen. Retrofitting this at M7 would mean rewriting every component; it is
specified here so components are built drawer-aware from the first commit.

Minimum supported width: **360px**. Touch targets: **44×44px** minimum on all interactive elements
below 768px, per WCAG 2.5.5.

**The mobile keyboard is a designed state, not an accident.** It is the most common way a mobile
chat UI breaks, so it is specified:

- The app shell uses `100dvh`, never `100vh`. On iOS Safari `vh` ignores the browser chrome and
  puts the composer under it.
- The composer is pinned to the visual viewport, not the layout viewport. When the keyboard opens
  the composer sits on it and the conversation scrolls behind; the composer is never covered.
- Opening the keyboard **pins the conversation to its last turn** rather than preserving scroll
  offset. Preserving the offset in a shrinking viewport scrolls the user away from what they were
  reading.
- The evidence drawer and the keyboard are mutually exclusive: opening the drawer blurs the
  composer and dismisses the keyboard, so the two never fight over the same screen space. (This
  was written when the drawer was a bottom sheet, where a half-height sheet above a keyboard was
  the concrete failure mode being ruled out — the drawer is a right-side sheet now [§16,
  2026-08-09], but dismissing the keyboard on open is still correct: it's still an overlay
  competing for the same viewport.)

**The timeline rail at 390px** covers a full history in ~340px of scrollable width, so a
300-day account cannot render one bar per day (≈1px each, unreadable and untappable). Below
768px the rail **buckets by week**, keeps changepoint markers at full size as the primary
affordance, and remains horizontally scrollable with the `now` marker pinned right. Density is
still legible; the resolution is honestly lower.

### 5.9 Accessibility standards

Target: **WCAG 2.2 Level AA**, with these specific commitments.

| Area | Commitment |
|---|---|
| Contrast | Every text/background pair in §5.2 meets 4.5:1; large text 3:1. No exceptions, including placeholder and disabled text (disabled uses `--faint` + reduced opacity on the *container*, never lighter text). |
| Color independence (1.4.1) | Provenance = fill vs hairline outline. Confidence = 4-segment meter. Status = icon + text. **Nothing is distinguished by hue alone.** |
| Keyboard | Every interactive element reachable and operable. Citation chips are real `<button>`s. The engine pane is a landmark with a skip link. Dialogs and drawers trap focus and restore it on close. |
| Focus visible (2.4.11) | 2px `--signal` outline at 2px offset. Never `outline: none` without a replacement. The focus ring is the one other place `--signal` is allowed to appear. |
| Motion (2.3.3) | `prefers-reduced-motion: reduce` honored globally (§5.6). |
| Semantics | Evidence rows are a `<ul>`, not divs. The trace is a `<figure>` with a `<figcaption>`. SQL display is `<pre><code>`. Stats are a `<dl>`. |
| Live regions | Streaming narration announces via `aria-live="polite"`, not `assertive`. New insights arriving via SSE announce once, debounced. |
| Testing | `@axe-core/playwright` runs against all four E2E paths. A new violation fails CI. Accessibility is verified, not asserted. |

---

## 6. Component Language

Every component below is specified as: **anatomy → states → rules**. States always include the
three that Phase 6 treats as features: empty, loading, error.

### 6.1 Buttons

Four variants. No others may be created.

| Variant | Surface | Text | Border | Use |
|---|---|---|---|---|
| `primary` | `--foreground` (inverted) | `--background` | none | The one action on a screen. Never two on the same view. |
| `secondary` | `--surface-2` | `--foreground` | `--border` | Everything else |
| `ghost` | transparent | `--muted-foreground` | none | Toolbar, icon buttons, dismissals |
| `danger` | transparent | `--invalid` | `--invalid` | Destructive only |

Sizes: `sm` 28px, `md` 32px, `lg` 40px height. Padding-x = `space-3` / `space-4` / `space-4`.
Radius `radius-sm`. Label is `text-dense` (sm/md) or `text-body` (lg), weight 500.

**Rules.** Never a gradient. Never a pill. Never a shadow. Hover changes background one surface
step in `duration-micro`. Active state is a 1px inset, not a scale transform. Disabled reduces
container opacity to 0.5 and sets `cursor: not-allowed`; it does **not** lighten the text (contrast).
Loading state swaps the label for a 14px spinner and keeps the button's width fixed so layout does
not shift.

### 6.2 Inputs

Anatomy: optional label (`text-micro`, mono, uppercase, `--faint`) → field → optional hint or
error (`text-meta`).

Field: `--surface` background, 1px `--border` border, `radius-sm`, `text-body`, 32px min-height,
`space-3` padding-x. Focus: border → `--border-strong`, plus the 2px `--signal` focus ring.
Error: border → `--invalid`, message in `--invalid` at `text-meta` with a 12px alert icon.

**The composer is the exception and the most important input in the product.** It is a
`<textarea>` that auto-grows from 1 to 6 rows, `radius-md`, `--surface` on `--bg`, with the
placeholder `Ask anything, or log it — meals, workouts, sleep, reports…`. `Enter` submits;
`Shift+Enter` newlines. It is never disabled during a response; it accepts the next message while
the previous one streams.

### 6.3 Cards

`--surface` background, 1px `--border`, `radius-md`, `space-4` padding. Optional header row with
`text-h3` title and right-aligned mono metadata.

**Rules.** Never lift on hover. Hover → `--surface-2`. Never nest a card in a card; use a divided
list instead. Cards do not have shadows (§5.5).

### 6.4 Tables

Used for evidence lists and any tabular history. Header row: `text-micro`, mono, uppercase,
`--faint`, 1px `--border` bottom border. Body rows: `text-dense`, `space-3` padding-y, hairline
dividers, hover → `--surface-2`. All numeric columns right-aligned with `tabular-nums`.

**Rules.** No zebra striping (it fights the hairline system). No vertical rules. Sortable headers
show direction with a 12px chevron, never with color. On mobile a table becomes a stacked
definition list; it never scrolls horizontally inside the page.

### 6.5 Dialogs

Base UI `Dialog`. `--surface-3` background, `radius-lg`, `--shadow-overlay`, max-width 480px
(560px for confirmations with detail). Backdrop: `oklch(0 0 0 / 0.6)` with a 2px backdrop blur.
Enter: opacity 0→1 and `translateY(4px)→0` over `duration-enter` with `--ease-out`. Exit: reverse
over `duration-medium`.

**Rules.** Focus trapped, restored on close. `Esc` always closes unless the dialog is a
destructive confirmation mid-flight. Title is `text-h3`. Never more than two actions in the footer.

### 6.6 Evidence chips *(the signature component)*

The clickable citation inside narration. This is the component the whole product is built to
deliver, and it has the strictest spec.

**Anatomy:** `[ date · label · value ]` — e.g. `Jun 2 · scan 21.4%`. Date and value in mono
(`text-meta`), label in sans (`text-dense`).

**Presentation:** inline-flex, `radius-xs`, 1px `--signal` border at 40% opacity, background
`--signal-dim`, `space-0.5` padding-y, `space-2` padding-x, baseline-aligned with the surrounding
prose.

**States**

| State | Treatment |
|---|---|
| default | As above. `cursor: pointer`. |
| hover | Border → full-opacity `--signal`, background lightens one step. `duration-micro`. |
| focus | 2px `--signal` ring, 2px offset. |
| active/selected | Solid `--signal-dim` at 28%, left 2px `--signal` bar. The linked evidence row is simultaneously highlighted (§5.6). |
| **invalid** | Border and text → `--invalid`, plus a 10px strikethrough-circle icon and `title="citation could not be resolved"`. Never silently dropped. |
| unresolvable | `--faint`, `cursor: not-allowed`, dashed border. The memory was superseded or removed; the batch API reported it in `missing`. |

**The third citation state has a home too.** `citation_report.status` is a three-way value, and
`uncited` — the answer cited *nothing* while evidence was available — is not a chip state, because
there is no chip to mark. It renders as one `text-meta` line beneath the answer, in `--faint`:
`answered without citing evidence · N memories were available`, with the count in mono and the
whole line clickable to open the pane.

It is deliberately quiet rather than alarming. An uncited answer is a narrator weakness, not a
system failure, and the honest reading is "this claim is unbacked", not "something broke". But it
is never hidden: a narrator that stops citing and gets away with it silently is exactly the
failure the mechanical validator exists to catch.

Note that `valid` covers the no-markers-and-nothing-citable case, so an empty-context answer like
"no logged data in that window" must **not** render this line. Crying wolf on the most common
empty state would train users to ignore the signal.

**Rules.** A chip is always a real `<button>`, never a `<span>` with a click handler. Chips never
wrap mid-chip; use `white-space: nowrap` and let the line break before them. Chip text comes from
the hydrated memory row, never from parsing model output (ADR-12).

### 6.7 Receipts

The inline confirmation after an ingestion turn. Proof that talking *is* logging.

**Anatomy:** `✦ 1 memory created:` then a row of mono detail tags: `meal` `lunch` `46g protein`
`conf 0.9`, then `+ embedding`, then a `view in engine →` link.

`text-meta`, mono for every value, `--muted-foreground` for the frame text, `--foreground` for values. Tags are
`radius-xs`, 1px `--border`, `--surface-2`. The `✦` mark is `--signal` and is the *only* place a
non-evidence element may use the accent, because a receipt **is** evidence of a write.

**Failure receipt** (never-lose-input, ADR-16A): `✦ saved — parsing incomplete` with a
`note` tag and a `retry extraction` action. The tone is factual. This state must look **as
designed** as the success state; it is a first-class outcome, not an error.

**Rules.** Never animates in with a bounce or a scale. Fades in over `duration-short`. Never uses a
green checkmark.

### 6.8 Timeline

The permanent memory-density strip, full width, 72px desktop / 48px tablet / 40px mobile rail.

**Anatomy:** graph-rule background (§4.4) → per-day density bars → changepoint markers
(`◆ May 12 protein ↑`) → a `now` marker pinned right.

Bars are `--faint` at a height proportional to `n`; days containing an insight get a 2px
`--signal` cap. Hovering a day shows a mono tooltip with the exact date and counts — this is the
*only* place a date renders. An earlier version also drew a permanent month-tick label
(`Jan`, `Feb`, …) under the rail; on a real account whose history spans many months, those
labels sit close enough together to overlap into unreadable text, and a hover tooltip already
answers the same question on demand. Removed rather than throttled to every Nth month, since a
chart is not the place to invent a rule for which months get skipped.

**Rules.** Data comes from `GET /api/timeline` (`{day, n, insights}`), aggregated in SQL. Never
ship a year of rows to count them client-side. Hand-rolled SVG, no chart library (§12).
**Empty:** the rule grid and a centered `text-meta` line, `your memory starts here`, with the
`now` marker at the left edge. It must read as *day one*, not as a broken widget.

### 6.9 Empty states

Every empty surface gets a designed state. Five exist:

| Surface | Copy | Affordance |
|---|---|---|
| Conversation, empty account | `Your memory starts here.` / `Tell me what you ate, how you trained, how you slept. I'll structure it and remember it.` | Three example prompts as `secondary` buttons |
| Conversation, returning account + fresh thread | `Ask something.` / `Everything you've logged is already in memory — ask about it, or log more.` | `Logo` at 112px, animated |
| Engine pane | `Nothing retrieved yet.` / `When you ask a question, every row the answer used will appear here.` | — |
| Timeline | `your memory starts here` | — |
| Stats bar | `0 memories · 0 days · 0 insights` in mono | — |

**Rules.** Never an illustration. Never "No data available." The stats bar shows honest zeros
rather than hiding, because `{memories: 0, insights: 0, days: 0, first_event: null, last_event: null}`
is a well-formed answer and the shape is already pinned by test. Copy is always two lines: a
statement, then what to do about it. **The returning-account empty conversation is the one
exception to "never an illustration"** (added 2026-08-09, explicit instruction, §16 Decisions
Log) — `Logo` there is the brand mark reused, not decoration standing in for a copy decision:
the two lines of copy are still present and still carry the state, same as every other row.

### 6.10 Loading states

Three kinds, and using the wrong one is a bug.

1. **Skeleton** — for content whose shape is known: evidence rows, turn history, stats. A
   `--surface-2` block at the exact final dimensions, with a 1.4s shimmer that is disabled under
   reduced motion (becomes a static block). Never a spinner for known-shape content.
2. **Streaming** — for narration. Text appears token by token with a 2px `--signal` caret. This is
   not a loading state, it is the answer arriving; treat it as content.
3. **Spinner** — only for indeterminate actions with no known shape: sign-in, retry extraction.
   14px, 1.5px stroke, `--muted-foreground`.

**The slow-Bedrock path is a designed state, not a fallback.** In place of the pending turn, a mono
`text-meta` label tracks the current stage — `retrieving…`, then `assembling context…`, then
`generating…` — each a real `event: stage` SSE frame (M6), never a timer pretending to be
progress. Amended 2026-08-10 (Decisions Log), superseding the 2026-08-09 connected-dot-trail
version: only the latest stage is ever on screen, swapping the previous one out in place (a fade/
slide handoff) rather than the two stacking into a growing, `--border`-connected list. The single
pulsing `--signal` dot beside it (the one other place besides rule 7's fixed list this token
already appears, kept for continuity rather than introduced here) marks "still working," not which
stage. The `Logo` mark next to this row runs at 36px and is the only place in the conversation it
animates — once a turn's answer is complete, `TurnBlock` renders a plain 36px spacer instead of the
mark (no static or animated logo on a finished turn), so the heartbeat/spin reads unambiguously as
"generating," never as decoration on an answer that already arrived. One of the four required E2E
paths tests this.

### 6.11 Error states

| Kind | Treatment |
|---|---|
| Field | Inline, `--invalid`, below the field, `text-meta` + icon |
| Turn-level | An assistant-side card with `--invalid` left border: what failed, what was preserved, one action. Ingestion failures always state that the input was saved. |
| Pane-level | Engine pane shows `couldn't load evidence` with a `retry` ghost button. The conversation stays usable. **A failed pane never breaks the chat.** |
| Route-level | Full-page state with the mark, one `text-h2` line, and a `reload` action |
| Partial hydration | When `POST /api/memories/batch` returns a non-empty `missing` list, render the resolved rows normally and one `text-meta` line: `1 cited memory is no longer available`. Never fail the whole pane over one stale chip. |
| **Session expired (401)** | See below. The one error state with its own rule. |

### 6.11.1 Session expiry

Any 401 renders **one `text-meta` line directly above the composer**, in `--faint` with a
`sign in` action: `your session ended · sign in to continue`. Three things must hold:

1. **The typed message survives.** Whatever is in the composer stays in the composer, and sends
   after re-auth. This is the never-lose-input guarantee (ADR-13.5) reaching the auth boundary —
   the product works hard never to discard what you told it, and the session layer must not be the
   one place that breaks the promise.
2. **The conversation stays on screen.** It is read-only until re-auth, not cleared. Wiping the
   thread reads as a crash.
3. **No redirect, no modal.** Both destroy context; a redirect additionally throws away the draft.

Re-auth happens in a dialog over the app. On success the notice disappears, queries refetch, and
the pending message sends. Nothing is lost and nothing moved.

**Rules.** Never "Something went wrong." Always name what failed and what survived. Never a modal
for a recoverable error. Errors use `--invalid` and an icon, never color alone.

### 6.12 Toasts

**Base UI `Toast`.** Bottom-right desktop, bottom-center mobile above the composer. `--surface-3`,
`radius-md`, `--shadow-popover`, max 400px, `text-dense`. Auto-dismiss 5s (never for errors, which
persist until dismissed). Max 3 stacked; older collapse.

**Rules.** Toasts are for *asynchronous* outcomes only (a background consolidation finished, a
retry succeeded). Anything the user just did synchronously gets inline feedback, not a toast. Never
use a toast for a validation error.

### 6.13 Navigation

This product is deliberately **navigation-light**. It carries **two** product surfaces and no
more: `Today` (the briefing, §6.20) and `Chat` (the conversation, §9). No sidebar of sections, no
nested routing beyond those two, and nothing that reads as a dashboard tab bar.

**Amended 2026-08-12** (§16 Decisions Log) — this section previously read "one screen plus
marketing and auth… no tab bar". Today made that false, and the count is now a stated ceiling
rather than an accident: the competitive read (§16) found the median mature product runs three to
five primary items and Oura shipped a redesign *removing* two. Two is where this product stops
until a surface earns a third.

**Top bar (56px):** mark + wordmark (left) · **primary nav** (Today · Chat) · stats in mono
(center-right) · connection indicator · account menu (right). The nav renders as a 1px bottom
border on the active item — never a filled pill or a chip row, which at 56px reads as chrome
competing with the stats beside it. The connection indicator is three 4px dots showing
CockroachDB health: `--faint` idle, `--signal` on active query, `--invalid` on error. It is the
top bar's one piece of ornament and it is functional. Two `ghost`/`sm` controls sit at the far
right: `Profile` (→ `/app/profile`, §6.19) beside `Sign out` — two adjacent actions, not a
dropdown menu (no third item has ever needed one).

Routes: `/` (landing), `/app/today` (§6.20), `/app` (the conversation), `/app/profile` (§6.19),
`/onboarding` (§6.19), `/login`, `/signup`. That is all.

**Where each entry point lands, and why they differ.** Login → `/app/today`: a returning account
has memory, and the briefing is what someone opening the app wants before they have a question.
Signup → `/onboarding` → `/app`: a brand-new account has nothing to brief, and the guided first
turn (§9.1) *is* the live product experience under ADR-13.4. The asymmetry is the product
behaving correctly, not an inconsistency to tidy up.

### 6.14 Icons

**Lucide React**, locked at `strokeWidth={1.5}` and 16px in dense UI / 20px in body / 24px in
marketing. 1.5 rather than the 2 default: at 16px a 2px stroke is chunky and fights the hairline
system.

**Rules.** Icons never appear alone without an accessible label. Icons are never colored except
`--invalid` for error states. No icon in a colored circle, ever. No emoji.

### 6.15 Charts

Two chart types exist, both hand-rolled SVG in `web/src/components/chart/`:

1. **Density bars** (the timeline strip, §6.8).
2. **Series line** (the money shot: protein and body-fat over time with a changepoint marker).

Series line: 1.5px stroke in `--muted-foreground`, the *subject* series in `--signal`, changepoint as a
`◆` at the axis with a mono label, axis labels `text-micro` mono, gridlines at 4% opacity matching
the graph rule. No legend if the series can be labeled inline. No tooltip that requires hover on
mobile; tap reveals a pinned readout.

**Rules.** No chart library (§12). No default palettes. Every chart reads its colors from the
theme tokens. Charts are `role="img"` with an `aria-label` summarizing the trend, plus a visually
hidden `<table>` of the underlying values (this is how a screen reader gets a chart).

### 6.16 Command palette

`⌘K` / `Ctrl+K`. Built on **Base UI `Dialog` + `Combobox`**, not `cmdk` (§12).

Anatomy: search field → grouped results (Actions · Recent turns · Memories) → footer hint row with
mono key caps. Results are `text-dense`; matched substrings get `--signal` text, not a highlight
background.

Actions: `Ask a question`, `Log a memory`, `Jump to date…`, `Open evidence for last answer`,
`Copy trace JSON`, `Sign out`.

**This component is explicitly cut-eligible.** It ranks below every item in the Phase-6 priority
list. If time compresses, it goes, and the keyboard shortcuts in §11 remain.

### 6.17 Auth screens (`/login`, `/signup`)

The one screen between the landing page and the product. It gets a spec because "engineer
improvises a form" is how the transition from a considered landing page to a considered product
gets broken by a generic centered card.

**Layout.** Single column, `max-width: 380px`, positioned **left of center** (`margin-left:
max(6vw, 48px)`) on the full dark page, vertically centered. Not a card, not a box, not centered:
no `--surface` panel, no border, no shadow. The form sits directly on `--background`. The graph
rule (§4.4) fades in from the right third of the viewport.

Centering this form would be the single most conventional choice available, and §3's anti-pattern
list rules out centered-everything for a reason. Left-weighting matches the hero (§8.2) so the
signup screen reads as the same product, not a bolted-on auth page.

**Anatomy, top to bottom**

```
[mark]                                    16px, --signal

Start your memory                         text-h2, --foreground
                                          (login: "Welcome back")

EMAIL                                     text-micro, mono, uppercase, --faint
[________________________]                input, §6.2

PASSWORD                                  text-micro, mono, uppercase, --faint
[________________________]                input, §6.2

[ Create account ]                        button/primary, full width, lg

Already have an account? Sign in          text-dense, --muted-foreground, link

every account starts empty — including     text-meta, mono, --faint
yours                                      (signup only)
```

That last line is doing real work. It sets the ADR-13.4 expectation *before* the user arrives at
an empty app, and it reframes a limitation as a statement of integrity. It appears on signup only.

**States**

| State | Treatment |
|---|---|
| default | As above. Submit disabled until both fields are non-empty. |
| submitting | Button label swaps for a 14px spinner, width fixed, fields go read-only. |
| email already registered | Inline under the email field, `--invalid`, `text-meta`: `that email already has an account · sign in instead` with the second half as a link. Never a toast. |
| invalid credentials | One line under the password field: `email or password is incorrect`. Deliberately does not say which — naming it confirms an account exists to anyone probing. |
| password too short | Live under the field once touched: `at least 8 characters`. Never blocks typing. |
| network / 5xx | Above the button: `couldn't reach the server · retry`. The form keeps its values. |

**Rules.** Labels are always visible — never placeholder-as-label (a placeholder disappears
exactly when it is needed). `autocomplete="email"` and `"current-password"` / `"new-password"`.
Enter submits from either field. Errors are inline and near their field, never a toast and never a
modal. The password field has a show/hide ghost toggle with an `aria-label`.

### 6.18 Search

There is no global search field. **Search in this product is the conversation** — asking "when did
I last complain about my knee?" *is* the vector search, and answering it with cited evidence is
strictly better than a results list. Adding a search box would be admitting the chat does not work.

The one exception is inside the command palette, which searches turns and memories by substring for
navigation, not for recall.

### 6.19 Profile intake & Profile settings *(added 2026-08-11, supersedes §13's original rejections)*

Two surfaces, one data model (ADR-17). Same visual grammar as §6.17 — no wizard chrome, no card,
no progress dots — because a profile form dressed as a SaaS "step 2 of 4" is exactly the generic
pattern §3 exists to rule out.

**`/onboarding` — the intake screen.** Shown once, immediately after signup succeeds, before the
guided first turn. Left-weighted layout identical to §6.17 (same `max-width: 380px`, same
left-of-center position, no `--surface` panel). Not a route the user returns to: once
`onboarded_at` is set, `/onboarding` redirects to `/app` like a used-up link.

Anatomy, top to bottom:

```
[mark]

Tell us a bit about you                   text-h2, --foreground
So AyuMind can compute targets            text-lead, --muted-foreground
that are actually yours.

NAME · DATE OF BIRTH · SEX (skippable)    required, compact grid
HEIGHT · CURRENT WEIGHT                   required
GOAL (card-select, 5 options)             required
ACTIVITY LEVEL (5-rung selector)          required

~148g protein · ~2,150 kcal               font-mono, --signal, appears live
based on your profile — adjust anytime    text-meta, --faint (the calculation basis,
                                           expandable to the full basis string)

▸ More about you (optional)               disclosure, collapsed by default
  UNITS · TARGET WEIGHT · DIET · ALLERGIES

[ Continue ]                              button/primary, full width, lg
Skip for now                              text-dense, --muted-foreground, link
```

The computed target line is the payoff, not a confirmation step: it renders the moment the
required fields resolve to a computable target (§6.15's mono-for-database-values rule extends
here — a target is an engine-computed number, so it is mono, same as an evidence figure). Tapping
it expands the one-line basis ("Mifflin-St Jeor BMR (male, age 29) × moderate activity (1.55×) …")
— never a bare number with no explanation, per DESIGN.md's broken-rules warning about hardcoded
values standing without provenance.

**Skip is real.** It submits whatever was filled (even nothing but the email already on file) and
sets `onboarded_at`. No field blocks it, and skipping never revisits the user with a nag — the
account behaves exactly as an unskipped one with fewer inputs, same as every other honest-empty
state in this product (§6.9).

**`/app/profile` — Profile & goals.** Reached from the account menu (§6.13's top-bar right side,
where the menu already lives). Same page shell as `/onboarding` reused for editing rather than a
distinct settings-screen design — sections: Identity, Goals, Nutrition targets, Dietary
preferences & allergies, Injuries/notes, and a closing explainer line: *"Your protein target uses
your weight, activity level, and goal. Changing it here only affects targets going forward — past
days are judged against the target that was active then."* That sentence is not decoration; it is
the user-facing statement of the historical-integrity rule ADR-17 enforces structurally
(`profile_change` history).

Editing **current weight** here submits through the same path as logging it in chat — a new
`weight` memory, not a mutated field — so the input control is intentionally styled like a
compact log entry ("log a new weight"), not a settings field, to avoid implying it overwrites
something.

**Rules.** Every required field carries a visible label (§6.17's placeholder-as-label ban
applies here too). The suggested-target line always reflects the *current* form state, live,
without a submit round-trip — it is pure client-side arithmetic mirroring `engine/profile.py`'s
formula, confirmed by the server response after submit. No field on either screen collects
anything from §13's medical-history/phone/address/wearable-credentials "should not collect" list.

### 6.20 Today *(added 2026-08-12, research P0 #1)*

`/app/today`. The home screen, and **a briefing, not a dashboard** — the banned word (§16) applies
here with more force than anywhere else, because this is the surface that most invites the
category default.

**Why the category's hierarchy is unavailable to us.** Every product studied (MyFitnessPal,
Google Health, WHOOP, Oura, Apple Health) fills 8 AM with *data collected while the user slept* —
a sleep score, a recovery number, a readiness dial. AyuMind has no overnight sensor, so copying
that opening would mean inventing the numbers. Today leads with **continuity** instead, which is
the one thing a memory product has and a sensor product does not: how much memory exists, where
you stood when we last measured, and one way to change it.

**Order, and nothing may invert it:**

| # | Zone | Job |
|---|---|---|
| 1 | **State line** | The thesis in one sentence. Days of memory · memory count · yesterday's protein against target · coverage. Every figure from `GET /api/today`. |
| 2 | **Targets** | Two — protein and energy. Never four. |
| 3 | **Composer** | The primary action. Above the fold at 390×844 (measured: ~450px above it). |
| 4 | **What changed** | The newest active insight, or *absent entirely*. |
| 5 | **Recently logged** | Receipts, and the way into the day view. |

**The four-state target rule.** A `TargetBar` has four states, and collapsing any pair is the bug
the component exists to prevent: *nothing logged* renders `— / 150 g` with an empty rail and the
words "nothing logged yet"; *a real logged zero* renders `0 / 150 g`; *a value with no target*
renders the number alone; *neither* points at Profile. At 8 AM the first row is the normal case,
which is exactly why it must not look like failure. A red ring at zero is the reflex this refuses.
Backed by contract: `GET /api/today` sends `value: null`, never `0`, and carries `has_data`
explicitly — `value === 0` and `value === null` are both falsy in JS, and that is precisely how a
fabricated zero reaches the most-seen screen in the product.

**No color encodes progress.** Bar fill is `--muted-foreground` on a `--surface-3` rail — the same
meaning-free neutral `ConfidenceMeter` uses, legible in grayscale. Rule 4 stands: `--signal` means
*evidence you can open*, and a progress bar is not evidence.

**Insight strength reuses `ConfidenceMeter`**, never a percentage (ADR-13.12) — one 0–1 visual
language, not two. The card shows `flagged` (the insight's `created_at`) *separately* from the
window the claim is about: the bi-temporal argument is invisible unless both clocks are on screen.

**It owns no machinery of its own.** The composer hands its message to `/app` via `location.state`
and `AppScreen` sends it through the same graph every turn uses; clicking a memory or an insight
opens the day view the timeline strip already owns (§9). One turn pipeline, one day-view renderer.
A second diary here would fork the definition of "a day" between two screens.

**It shows its work.** Today asserts figures nobody asked for, so it carries the same
`RetrievalQueries` disclosure a turn does — the five statements that produced every number
(ADR-12). This is the reason the endpoint returns its `RetrievalStep`s at all.

**Explicitly not on this screen:** sleep, recovery, readiness, stress, HRV, steps, water (no
sensor — each would be fabrication); habit grids, streak flames, badges, celebrations,
motivational copy, any greeting with an exclamation mark (§2's voice table rejects these by name).
Weight is a line with a date, not a card.

**Coverage, not a streak.** "5 of the last 7 days logged" is the same quantity that gates
`analytics.pattern_strength`, stated as what it is: how much the numbers above rest on. Three of
five competitors ship streaks and it plainly works for them; it cannot work here, because §1
defines this product *against* rings and green checkmarks. Same user need, opposite framing.

---

## 7. Landing Page Philosophy

### The thesis

Most SaaS landing pages describe a product. **This one runs it.** The single most persuasive asset
we have is a real cited answer resolving to real database rows, and it is more compelling than any
gradient mesh, 3D illustration, or feature grid we could build.

The landing page is therefore not an introduction to the product. It is the product's first
screen, with the marketing arranged around it.

### Constraint, stated plainly

ADR-13.7 locks us to a Vite + React SPA served as static assets by FastAPI. Linear, Vercel, Stripe,
and Raycast all run SSR/SSG marketing behind a CDN. We cannot match their first-paint numbers, and
we should not try. **We match their craft instead**: typographic contrast, restraint, real content,
and motion that responds to input rather than playing on load.

The landing page is a lazy-loaded route in the same SPA, sharing the same `@theme` tokens. A
separate static page would paint faster but would fork the design system into two places that
drift, and drift is the thing this document exists to prevent.

---

## 8. Landing Page Specification

### 8.1 Section flow

Seven sections, vertical, no carousel, no tabs.

| # | Section | Job |
|---|---|---|
| 1 | **Hero** | Make the claim and prove it in the same viewport |
| 2 | **The forgetting** | Establish the problem: assistants start over every time |
| 3 | **The money shot** | Interactive replay of the time-travel question |
| 4 | **How memory is built** | Three movements: capture → structure → consolidate |
| 5 | **Why not a vector store** | The SQL+vector argument, rendered |
| 6 | **Honest by construction** | Confidence, provenance, retraction. The trust section. |
| 7 | **CTA** | One action |

### 8.2 Hero

**Copy**
```
It remembers.
And it can prove it.

A health companion with a real memory: every meal, workout, and scan
becomes a queryable row. Ask what changed, and get the receipts.

[ Try it ]   [ See the glass box ↓ ]
```

**Typography.** `text-display-l` (72px / 500 / −0.035em), two lines, left-aligned, `--text`. The
second line is `--muted-foreground` at the same size — the claim is bright, the proof-of-claim is quiet.
That one tonal shift is the whole brand in two lines. Sub-copy at `text-lead` `--muted-foreground`,
max 52ch.

**Layout.** Full-bleed, `space-32` vertical padding, content on the 1120px grid, left-weighted.
Graph rule behind, fading out toward the bottom. **No centered hero.**

**The hero visual** is a live, non-interactive replay of a real cited answer: prose typesets in,
then two citation chips resolve, then a compact evidence row appears with its provenance and
confidence. It runs once on entry, then holds. It is the product, not a screenshot of the product.

Under `prefers-reduced-motion` it renders in its final state immediately.

### 8.3 Storytelling flow

The page is an argument in five beats:

1. *You already know this problem.* (§2 — the forgetting)
2. *Here is what it looks like when it is solved.* (§3 — money shot)
3. *Here is how that is actually built.* (§4 — three movements)
4. *Here is why the obvious approach does not work.* (§5 — why not a vector store)
5. *Here is why you can trust the answer.* (§6 — honesty)

No testimonials (we have none, and fake ones are disqualifying). No logo wall. No pricing. No
"Built for X" copy.

### 8.4 Section specs

**§2 The forgetting.** Two short chat fragments side by side: a generic assistant answering "what
did I eat in June?" with an apology, and this product answering with an aggregate. `text-display-s`
heading: `Most assistants meet you for the first time, every time.` Diff-style, no animation beyond
a scroll-triggered fade.

**§3 The money shot.** The centerpiece, full-bleed, `--surface` band. A scroll-driven, four-step
replay the user controls by scrolling: the question → the plan (which tools ran) → the evidence
rows appearing → the narrated answer with chips resolving. The actual SQL is visible in `<pre>`.
Heading: `Ask what changed. Get the rows that prove it.`

This is the section judges will remember. It gets the most build time of anything on the page.

**§4 How memory is built.** Three movements as full-width horizontal bands, **not a three-column
icon grid**. Each band: a mono step number (`01`), a `text-h2` title, two lines of body, and a
small live diagram on the right. Capture → Structure → Consolidate.

**§5 Why not a vector store.** A two-row comparison, honest and specific: `"when did I complain
about my knee?"` (vector search wins) versus `"protein in June"` (`SUM … GROUP BY week` — vector
search cannot do this at all). Both queries shown in mono. Heading:
`Some memories you retrieve. Some you have to compute.`

**§6 Honest by construction.** Three artifacts shown as real UI, not claims: a confidence meter, a
`reconstructed` provenance tag beside a `live` one, and a retracted insight with its retraction
condition visible. Heading: `It tells you when it is not sure.`

**§7 CTA.** One `primary` button: `Start your memory`. Beneath it, one mono line:
`every account starts empty — including yours`. That line is doing real work: it sets the
expectation ADR-13.4 requires and turns a limitation into a statement of integrity.

### 8.5 Scroll and micro-interactions

**Scroll animations.** Sections fade and rise 12px on entry, `duration-enter`, `--ease-out`, once
only (never re-trigger on scroll-up). Threshold 15%. Implemented with `IntersectionObserver`, not a
scroll listener.

The money-shot replay (§3) is the one **scroll-linked** element: progress through its four steps
maps to scroll position within the section, so the user drives it. Under reduced motion it becomes
four static stacked panels.

**Micro-interactions**
- Buttons: background shift only, `duration-micro`. No scale, no glow.
- Citation chips in the hero: border brightens on hover, exactly as in the app. Consistency between
  marketing and product is itself a quality signal.
- The mark in the nav: the inner square rotates 90° over `duration-medium` on hover. One playful
  detail, once.
- Links: 1px underline that grows from left over `duration-micro`.
- Nav: transparent at top, gains `--surface` background and a `--border` bottom border after 24px of
  scroll, `duration-medium`.

**Premium details that actually matter** (in priority order): optical alignment of the hero text to
the grid, not mechanical; `text-wrap: balance` on all headings; consistent 24px baseline rhythm;
`scroll-behavior: smooth` gated on reduced motion; a real 404; correct `<title>` and OG tags;
favicon at every size; no layout shift on font load (metric-adjusted fallback via
`size-adjust`).

### 8.6 Landing responsiveness

| Range | Behavior |
|---|---|
| ≥1280px | Full spec. Hero at 72px. Money shot scroll-linked. |
| 768–1279px | Hero drops to `text-display-m` (56px). §4 bands stack their diagrams below the text. Money shot keeps scroll-linking. |
| <768px | Hero at 40px (`text-display-s`), `space-12` block rhythm. **Money shot becomes four stacked static panels with a horizontal swipe between evidence rows** — scroll-linked animation on mobile fights native scroll and is dropped deliberately. Nav collapses to mark + a single `Try it` button; no hamburger, because there are only four routes. |

Mobile is not a reduction of desktop here — the money shot is *rebuilt*, because the desktop
interaction is wrong on a phone.

---

## 9. Dashboard Philosophy

There is no dashboard. **There is a conversation with the engine visible beside it.**

This is the central decision of wireframe v3 and it survived two rejected drafts: v1 (chat-dominant)
made memory look like a sidebar afterthought; v2 (memory-dashboard-dominant) demoted the
conversation. The product is conversation-first with the Memory Engine *continuously visible*, and
the word "dashboard" is avoided in code, copy, and file names because it invites the v2 mistake.

### Information hierarchy

1. **The answer** — what the user asked for
2. **The evidence** — the rows that produced it
3. **The history** — where this sits in the timeline
4. **The system** — counts, connection, account

Nothing may invert this order. A stats widget that visually outweighs the conversation is a bug.

### Visual rhythm and density

The app is **dense on purpose**. Evidence rows are 13px with 12px padding; the engine pane fits
roughly eight rows without scrolling. The conversation is comparatively spacious (15/24, generous
turn spacing) so that reading is easy and scanning is easy, in the places where each matters.

That contrast is the app's rhythm: **loose where you read, tight where you verify.**

### 9.1 The first turn (the most important 30 seconds in the product)

**ADR-13.4 is what makes this critical.** There is no judge sandbox, no seed cloning, and no
sample-data onboarding. A judge signs up and gets a genuinely empty account. So the sequence
*signup → first message → first receipt* is not onboarding polish — it is the **entire live
product experience** for the person scoring it, and it is E2E path 1 in the Definition of Done.

A passive empty state fails here: a reviewer who does not know what to type sees an empty box,
types nothing, and leaves without the glass box ever running once.

**The sequence**

| # | State | What is on screen |
|---|---|---|
| 1 | Arrival | Conversation shows `Your memory starts here.` (`text-h2`) and one `text-lead` line: `Tell me what you ate, how you trained, how you slept. I'll structure it and remember it.` Three example prompts as `secondary` buttons: `250g curd, 3 eggs, 200g chicken` · `ran 5k this morning, felt easy` · `slept 6h, woke up twice`. The composer is **auto-focused**. |
| 2 | Typing | The three prompts fade to `--faint` at 40% over `duration-short`. They stay clickable; they stop competing. |
| 3 | Sent | The turn appears. Beneath it, the staged progress line (§6.10): `extracting…` then `writing…`, driven by real stage events. |
| 4 | **The receipt lands** | §6.7 receipt fades in over `duration-short`: `✦ 1 memory created:` `meal` `lunch` `46g protein` `conf 0.9` `+ embedding`. This is the moment the product proves itself. It gets the only entrance animation in the app shell. |
| 5 | The engine fills | The pane replaces `Nothing retrieved yet.` with the new row. Stats tick `0 → 1 memories`, `0 → 1 days`. The timeline gains its first bar. Three surfaces confirm one action, simultaneously. |
| 6 | Hand over | One `text-meta` `--faint` line under the receipt: `now ask about it — "how much protein today?"`. Appears once, ever. Dismissed permanently on the next send. |

**Rules**

- **The guided state exists only until the first memory is created.** After that the app is the
  app forever. This is not a tour, has no steps, no progress dots, no skip button, and nothing to
  dismiss except the single step-6 hint.
- **Step 5's three simultaneous updates are the point.** Talking became structured memory in three
  places at once. Do not stagger them for effect; they are one event.
- **If extraction fails**, the failure receipt (§6.7) carries the same weight: `✦ saved — parsing
  incomplete`. The never-lose-input guarantee is *more* impressive on first contact than a clean
  parse, and step 5 still runs — a `note` memory is a memory.
- Example prompts are real loggable text, never `Try asking about...` placeholders. A reviewer
  should be able to click one and get a genuine memory.

### Evidence-first UX

The engine pane is not a debug panel that a curious user can open. It is **always visible on
desktop** and it *follows the conversation* without being asked. Its header reads
`MEMORY ENGINE · LIVE — FOLLOWING CONVERSATION` in mono micro-type.

Every answer that used context has a trace, and the pane shows it. A turn with no trace is
honestly rendered as such (stage (G) is best-effort); the pane says `no context assembled for this
turn` rather than showing stale rows from the previous one. **Showing the previous turn's evidence
next to a new answer would be the single worst bug this product could ship**, because it would make
the glass box lie.

**Day view (added 2026-08-09, §16 Decisions Log) is the one deliberate departure from "follows
the conversation."** Clicking a timeline bar has no turn to follow — the memories on that day may
span multiple threads, or have no chat turn behind them at all — so the pane instead shows a raw
listing for that calendar day, fetched by `GET /api/memories/by-day/{day}` rather than derived from
any trace. It says so in its own header (the date, not "following conversation") and exits back to
the normal turn-following behavior on the next turn, a citation click, or its own explicit exit —
never silently, and never mixed with trace rows in the same view.

### Glass-box interactions

| Interaction | Result |
|---|---|
| Click a citation chip | The signature choreography (§5.6): chip activates, matching row highlights, pane scrolls to it |
| Click an evidence row | Expands to full payload JSON in mono, with `radius-md` and a copy button |
| Click `how this was retrieved` | Expands the executed SQL and vector queries in `<pre><code>`, with the parameter values shown |
| Click an insight | Shows lineage: the hypothesis, its supporting memory IDs, its confidence, and its retraction condition. Lineage is **rendered, never cited** (Q1, resolved narrow) |
| Click a timeline day | Scrubs the conversation to that date AND puts the engine pane into **day view**: every memory logged that day (a raw listing, not a retrieval trace — a bar has no query behind it), with a count and a "back to conversation" exit. Day view persists until a new turn is sent, a citation chip is clicked, or the exit is used |
| Hover a provenance tag | Tooltip: `live` = captured as it happened; `reconstructed` = rebuilt from records, with an estimated timestamp |

### Transitions

Panes never slide. Content within them fades and expands (`duration-medium`). The single
choreographed motion is the citation link. New evidence rows arriving via SSE fade in top-down at
40ms stagger, capped at 6 staggered items so a 40-row result does not take two seconds to appear.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Command palette |
| `/` | Focus composer |
| `Esc` | Close overlay / blur composer |
| `⌘Enter` | Send (when the composer is multi-line) |
| `E` | Toggle engine pane (tablet) / open drawer (mobile) |
| `T` | Focus timeline |
| `?` | Shortcut reference |
| `↑` in empty composer | Edit last message |

All shortcuts are discoverable via `?` and shown in the command palette footer. None are required
to use the product.

### Desktop / tablet / mobile

Per §5.8. The one thing worth restating: on mobile the engine pane is a **right-side drawer**
(Base UI `Drawer`, amended 2026-08-09 from an original bottom sheet — §16) that opens when a
citation chip is tapped, landing scrolled to the relevant row. The chip tap and the drawer open
are the same gesture, so the claim→proof link survives the smallest screen. That link is the
product; it is the last thing to be compromised.

---

## 10. Glass-Box UI Philosophy

The glass box is **load-bearing, not decoration**. Three reasons it exists:

1. It makes "Agentic Memory Design" **scoreable**. Judges see evidence rows and real queries
   instead of claims.
2. It renders the hybrid SQL+vector argument on screen — the why-not-Mem0 answer, visible.
3. It converts *"an LLM said a thing"* into *"a database proved a thing."*

### The data-source rule (ADR-12), as a UI law

> Everything structured on screen is rendered from the deterministic `EvidenceTrace` and the engine
> APIs. The model contributes prose and citation markers only. **Model output is never parsed to
> build glass-box data.**

Practically, for M4–M8: no component may derive a number, a date, an ID, a count, or a status from
the narration string. If a component needs a value, it comes from `/api/turns/{id}/trace`,
`/api/memories/batch`, `/api/stats`, or `/api/timeline`.

### The trace is served verbatim (I-29)

The trace is rendered exactly as stored. It is never merged with live data, re-derived, or
"refreshed". A glass box that can drift from what the turn actually did is **worse than no glass
box**, because it looks authoritative while being wrong.

The citation report is the one derived value, and it is a pure function of two persisted values
(the answer and `trace.citable_ids`), so it cannot drift.

### Honest scope (ADR-13.13)

Citation validation proves a cited ID is **resolvable**, not that the claim attached to it is
*true*. The UI must not overstate this. Copy says `citation resolves to memory <id>`, never
`verified`. An invalid citation is flagged visibly (§6.6) rather than hidden — the narrator being
caught is a feature.

### Insight lineage: rendered, never cited (Q1)

An insight's supporting memory IDs are shown when the user opens the insight, but they are **not**
in the citable set. The narrator saw the hypothesis, not the rows beneath it, so citing them would
be claiming a provenance that did not occur. Narrow can widen later; wide cannot narrow.

---

## 11. Technology Decisions

Recommended stack, then the honest verdicts on everything asked about.

### Adopt

| Library | Why | Used for |
|---|---|---|
| **Vite 7 + React 19.2 + TypeScript** | Locked by ADR-13.7 | The SPA |
| **Tailwind CSS v4** (`@tailwindcss/vite`) | In v4, tokens are CSS custom properties in `@theme` and utilities are generated *from* them. **`theme.css` is simultaneously this document's implementation and its single source of truth** — there is no second copy to drift. Kills `tailwind.config.js` and the PostCSS chain. | All styling and the token layer |
| **Base UI 1.6** (`@base-ui/react`) | From the teams behind Radix, Floating UI, and MUI. 40+ components, and critically **Dialog, Drawer, Toast, Combobox, Popover, Tooltip all share one portal and focus system**. shadcn made it the default in July 2026. | Every interactive primitive |
| **shadcn/ui** (Base UI variant) | Copy-in, not a dependency: we own the files, so re-theming is editing our own code. Taken for **behavior** (focus traps, keyboard nav, collision detection), with the visual layer replaced entirely by our tokens. | Component starting points |
| **Motion v13** (`motion/react`) + `LazyMotion` | `useReducedMotion` is a hard requirement (§5.6), and `LazyMotion` with `domAnimation` cuts ~31KB to ~5KB, which matters for the landing page's first paint. | The signature choreography, scroll reveals, presence transitions |
| **TanStack Query v5** | Makes `isPending` / `isError` / empty / data the four branches every call site is *structurally forced* to handle. Given that empty, loading, and error states are three named Phase-6 deliverables, this is the mechanism, not a convenience. Also: one `invalidateQueries` updates stats + timeline + insights when SSE pushes. | All five read endpoints |
| **Zod** | Runtime validation of API responses at the boundary. The backend is Python with no shared types; Zod catches contract drift at the fetch instead of as `undefined` three components deep, and infers the TS types for free. | `web/src/api/schemas.ts` |
| **React Router v7** (declarative) | Four routes. Nothing more is warranted. | `/`, `/app`, `/login`, `/signup` |
| **Lucide React** | Comprehensive, consistent grid, tree-shakeable, `strokeWidth` configurable (we lock 1.5). | All iconography |
| **`@fontsource/ibm-plex-mono`** | Self-hosted mono, no CDN. | Engine typeface |
| **`@playwright/test` + `@axe-core/playwright`** | The 4 E2E paths are already in the Definition of Done. Axe makes §5.9 verified rather than asserted. | CI |

### Reject, with reasons

| Asked about | Verdict | Why |
|---|---|---|
| **Aceternity UI** | **No** | It is the single largest source of the "generic AI landing page" look: glow beams, spotlight hovers, 3D tilt cards, aurora gradients. It also ships no `prefers-reduced-motion` handling by default. Adopting it would produce exactly the outcome you asked me to prevent. |
| **Magic UI** | **No** | Same aesthetic family, same convergence problem. A judge who has seen three hackathon entries has seen these components. |
| **React Bits** | **No, narrowly** | Genuinely the best of the three: 110+ components, no mandatory Motion dependency, and it *does* ship reduced-motion controls. But its value is decorative effects we have deliberately excluded, and the two things we might have wanted (number ticker, text reveal) are ~20 lines each against our own tokens. Not worth a dependency. |
| **Origin UI** | **No** | Good, broad shadcn-compatible registry, but it overlaps almost entirely with what Base UI + shadcn already give us. A second registry means two component idioms in one codebase. |
| **Vaul** | **No — it is unmaintained** | Confirmed 2026-08-07. shadcn is tracking migration away from it. **Base UI `Drawer` is stable at 1.3.0+ and shares the portal system with everything else**, which also eliminates the documented "Select inside Drawer breaks focus" bug class. This replaces my earlier recommendation. |
| **Sonner** | **No** | Excellent library, wrong stack. Mixing it with Base UI reintroduces a second portal system; the known failure is "closing a Sonner toast dismisses the Drawer". Base UI `Toast` is the coherent choice. |
| **cmdk** | **No** | Still Radix-internal, same portal conflict. The command palette is built on Base UI `Dialog` + `Combobox` (§6.16) — and it is cut-eligible anyway. |
| **Embla Carousel** | **No** | Nothing in this product is a carousel. The one horizontal gesture (mobile evidence swipe, §8.6) is a scroll-snap container, ~15 lines of CSS. |
| **Recharts / Tremor** | **No** | Both ship opinionated themes we would spend longer overriding than writing the charts. We need exactly two chart types (§6.15), both simple, both needing exact token control. Tremor also pulls Recharts underneath. Hand-rolled SVG in `components/chart/`. |
| **React Hook Form** | **No** | Two forms exist: sign-in and sign-up, each two fields. RHF is the right tool at ten fields with cross-field validation; here it is ceremony. Native form state + the Zod schemas we already have. |
| **TanStack Router** | **No** | Type-safe search params and route loaders are real wins at scale. At four routes with no meaningful URL state, it is a codegen step and a learning surface for benefit we cannot collect. Revisit if routing ever grows. |
| **Radix UI (direct)** | **No** | Superseded by Base UI for new work in this codebase. Do not mix the two. |
| **Storybook** | **No** | A second application to configure, style, and keep green. In the remaining time it consumes foundation budget and produces nothing a judge sees. |
| **CSS-in-JS**, **Redux / Zustand / Jotai** | **No** | Tokens live in `@theme`; server state is Query; UI state is `useState`. There is no fourth category in this app. |

### Build work this implies (not yet scheduled anywhere)

The repo has **no Node toolchain at all** today: `web/` holds one README, the [Dockerfile](Dockerfile)
has no build stage, [ci.yml](.github/workflows/ci.yml) has no frontend lane, and `.dockerignore`
does not know about `node_modules`. M4 must begin with: scaffold, Dockerfile node stage, CI
frontend lane, FastAPI static mount + SPA catch-all. Estimated half a day, and it is a prerequisite
for every other Phase-6 milestone.

### Open risk — resolved by M6, differently than planned

**SSE through Express Mode's shared ALB is unproven**, and the live engine pane depends on it.
This section originally planned a ~20-line spike against the deployed URL before building the
pane, with Query's `refetchInterval` as the fallback if SSE failed. Neither happened as written:
the deployed URL was never reachable (AWS access blocked throughout Phase 6 — see `TODOS.md`),
so there was nothing to spike against.

**What shipped instead (M6):** the pane was built directly on SSE, but the client
(`web/src/api/chatStream.ts`) detects a failed or incomplete stream **per request, at runtime**,
and falls back to the plain `POST /api/chat` for that turn — not `refetchInterval` polling, but
the same effect: a turn that cannot stream still completes normally, just without the live
progress line. This is a strictly better mitigation than the one planned here, because it does
not require knowing in advance whether the ALB cooperates, and it degrades per-turn rather than
for the whole session. The residual risk is unchanged in kind, only in size: this has been
verified against the real dev stack (raw SSE curl, 15/15 Playwright E2E, zero fallbacks observed
in five real turns) but never against the actual deployed container.

---

## 12. Design Rules — the contract for M4–M8

Every component must satisfy all twenty. A review that finds a violation blocks the merge.

**Typography and voice**

1. Only two typefaces exist. No third family may be introduced.
2. **Mono means the database said it.** Never use mono for emphasis, headings, or style. Never use
   sans for a memory ID, timestamp, confidence value, provenance tag, or SQL string.
3. No font size may be introduced between 24px and 40px. The gap is intentional.
4. Display type is weight 500. Never 700 above 24px.
5. Every numeral in a data context uses `tabular-nums`.

**Color**

6. No hardcoded colors. Every color comes from a `@theme` token. A hex in a component is a bug.
7. `--signal` means *evidence you can open*. It appears only on citation chips, the receipt `✦`,
   insight caps in the timeline, the subject series in a chart, and focus rings. Nowhere else.
8. Nothing is distinguished by hue alone (WCAG 1.4.1). Provenance is fill vs outline; confidence is
   a segment meter; status is icon + text.
9. Both dark and light themes are supported (added 2026-08-09, superseding the original M4–M8
   dark-only scope — see §16 Decisions Log). `[data-theme="dark"|"light"]` in `theme.css` is the
   only place literal color values may appear; every component still reaches them only through
   the same `@theme` tokens rule 6 already required. The landing page's hero is the one
   exception, pinned to dark on purpose because `MoltenMetal`'s colors are hardcoded — see its
   own entry in §16.

**Form**

10. Radii come from the five-step scale. `radius-full` is for the avatar and nothing else.
11. No shadows except `--shadow-overlay` and `--shadow-popover`, and only at depth 3. Cards do not
    lift; they change surface.
12. No gradients. Anywhere. On anything.

**Motion**

13. No animation exceeds 400ms.
14. Every animated component honors `prefers-reduced-motion`, and the reduced path must lose no
    information — only movement.
15. Scroll reveals fire once and never re-trigger.

**Structure and data**

16. **No component derives structured data from model output.** Numbers, dates, IDs, counts, and
    statuses come from the trace and the engine APIs (ADR-12).
17. The trace is rendered verbatim. Never merged with live data, never re-derived (I-29).
18. Every list of memories is hydrated through `POST /api/memories/batch` in **one** request. N+1 at
    the API boundary is a bug, not a style preference (T16).

**States and access**

19. Every data-driven component ships all four states: empty, loading, error, and populated. A
    component with only the populated state is incomplete and does not merit review.
20. Every interactive element is keyboard-operable with a visible focus ring, has an accessible
    name, and meets 44×44px touch targets below 768px. `@axe-core/playwright` must stay green.

---

## 13. Not in scope

Design decisions considered during the 2026-08-07 review and explicitly deferred.

| Deferred | Why |
|---|---|
| ~~Light theme~~ **Shipped 2026-08-09** | Was ~1 day for a second designed ramp with demo/video/judging on one screen; requested explicitly, so the "purely additive" structure paid off — see §16. |
| Reasoning lineage **graph** | A visualization project hiding in a bullet. First-to-cut per 07's build order; the text lineage list (§9, **shipped in M8**) is the shipped form. |
| Command palette (§6.16) | Specified but cut-eligible. Ranks below every item in the Phase-6 priority list; the keyboard shortcuts stand without it. |
| Password reset flow | Requires email delivery, which is infrastructure this project does not have and the demo does not need. |
| ~~Multi-thread conversation UI~~ **Shipped 2026-08-09** | Was "the demo never touches it"; requested explicitly. `thread_id` already existing in the API (the reason this was cheap to defer) is exactly why it was cheap to build — see §16. |
| Photo-ingest UI | **Not** this document's M7 (the timeline strip, shipped) — Phase 5's separately-numbered M7 (photo ingestion, the backend feature) is cut for the hackathon (see `TODOS.md`). The composer's camera affordance is not built in Phase 6 regardless. |
| ~~Onboarding tour / checklist~~ **Superseded 2026-08-11** | The *tour* stays rejected — no steps, no progress dots, no dismissible chrome, and the guided first turn (§9.1) is unchanged. What shipped instead is a one-screen profile **intake** (§6.19), a different object: it collects facts the engine needs, once, and is never shown again — not a walkthrough of the UI. |
| ~~Settings / profile screen~~ **Superseded 2026-08-11** | The original rationale — "no setting in this product changes anything a judge would see" — stopped being true the moment profile data started feeding computed nutrition targets. A profile screen that changes the number in `EvidencePane` **is** something a judge sees. Ships scoped to identity/goals/targets only (§6.19) — this is not a general settings surface. |

## 14. What already exists

Reuse these. Do not rebuild them.

| Asset | What it gives M4–M8 |
|---|---|
| [`web/src/styles/theme.css`](web/src/styles/theme.css) | Every token in §5, live and verified in-browser (Satoshi 15px body, mono tabular, signal `oklch(0.8 0.155 78)`) |
| [`web/src/api/schemas.ts`](web/src/api/schemas.ts) | Zod contracts for all five endpoints; TS types inferred, never hand-written |
| [`web/src/api/client.ts`](web/src/api/client.ts) | The single fetch boundary, with `ApiError.status` already available for the §6.11.1 401 path |
| [`api/spa.py`](api/spa.py) | Deep links and client routing already work in the container; 12 tests pin it |
| Wireframe v3 | Approved visual grammar; §5.7 is its measured form |
| `GET /api/stats` empty shape | `{memories:0, insights:0, days:0, first_event:null, last_event:null}`, already asserted by test — the exact contract §6.9 and §9.1 render |
| [`docs/engineering/frontend-guidelines.md`](docs/engineering/frontend-guidelines.md) | The code-structure contract, including the review checklist |
| Lucide + Base UI | Icon set and every interactive primitive; no component library decisions remain open |

## 15. M4 build order

Seven tasks from the 2026-08-07 `/plan-design-review`. Each derives from a numbered finding, not
from a wishlist. P1 blocks the phase; P2 lands in the same phase; P3 is a follow-up.

Numbered `F-T*` to stay clear of the T1–T18 backlog in
[11-implementation-tasks.md](docs/office-hours/11-implementation-tasks.md).

- [x] **F-T1 (P1, human ~4h / CC ~45min)** — first-run — Build the guided first-turn sequence (§9.1)
  - *Why:* ADR-13.4 gives judges an empty account with no seed data, so signup → first message →
    first receipt **is** the live product experience being scored. It was the least-specified
    surface in the plan.
  - *Verify:* E2E path 1 (signup → log → receipt → pane)
- [x] **F-T2 (P1, ~3h / ~30min)** — auth — Build `/login` and `/signup` to §6.17
  - *Why:* two of four routes had zero design; the default improvisation is a generic centered card,
    on the only screen between the landing page and the product.
- [x] **F-T3 (P1, ~15min / ~5min)** — layout — Cap conversation text at 72ch (§5.7)
  - *Why:* `minmax(560px, 1fr)` yields ~2000px lines of 15px text on the wide monitor a reviewer
    actually uses.
- [x] **F-T4 (P1, ~2h / ~20min)** — auth — 401 inline notice, preserve the draft (§6.11.1)
  - *Why:* nothing specified session expiry; a redirect would discard a typed message, which is the
    one thing this product promises never to do.
- [x] **F-T5 (P2, ~30min / ~10min)** — glassbox — Render `citation_report.status === "uncited"` (§6.6)
  - *Why:* the backend emits a three-way status; only two had UI.
- [x] **F-T6 (P2)** — responsive — Mobile keyboard: `dvh`, visual-viewport composer (§5.8) — **partial, recorded honestly**
  - *Why:* the keyboard covering the composer is the classic mobile-chat failure.
  - *Done:* the app shell already used `100dvh` (F2); added a feature-detected
    `visualViewport.resize` listener (`Conversation.tsx`) that re-pins to the last turn when the
    viewport shrinks — the keyboard-opening heuristic §5.8 specifies. Base UI `Drawer`'s own
    focus trap already moves focus off the composer when the drawer opens, satisfying "drawer and
    keyboard are mutually exclusive" without new code.
  - *Not done:* a `position: fixed`, `visualViewport`-tracked composer (the literal "pinned to the
    visual viewport" mechanism). `dvh` is the modern standard fix and covers iOS Safari 15.4+ /
    Android Chrome 108+; the more invasive fixed-positioning approach was not added because it
    cannot be verified against a real device in this environment, and shipping unverified
    viewport-tracking code risks being worse than the dvh baseline. Revisit with real-device
    access.
- [x] **F-T7 (P3)** — timeline — Bucket the rail by week below 768px (§5.8)
  - *Why:* a 300-day account renders 1px untappable bars at 390px.
  - *Done:* below 768px `Timeline.tsx` sums into 7-day buckets, renders them at a fixed 16px each
    inside a horizontally scrolling viewport, and keeps the `now` marker and hover tooltip pinned
    to the outer (non-scrolling) container so they read correctly at any scroll position.
    Verified: a dedicated mobile-viewport (390×844) Playwright test asserts the bucketed timeline
    renders with zero axe violations.

## 16. Decisions Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-07 | Design system created via `/design-consultation` | Frontend foundation locked before M4 so every React component has a contract |
| 2026-08-07 | Direction: **Instrument**, not wellness app | The product's material is evidence, not fitness. Category defaults (rings, green checks, purple gradients) would make the glass box read as decoration |
| 2026-08-07 | **Typeface as provenance** — Satoshi = model, IBM Plex Mono = database | Renders ADR-12 as visual grammar, learnable in ten seconds. Validated against Linear, which uses Berkeley Mono for exactly this purpose |
| 2026-08-07 | Amber `#FFB224` as the sole accent | Instrument readout. The AI-health field is purple/teal/green. Cost: forfeits amber as a semantic warning color |
| 2026-08-07 | Provenance and confidence encoded in **form**, not color | Satisfies WCAG 1.4.1 by construction and matches the instrument thesis |
| 2026-08-07 | Small radii (2/4/6/8), no pills | Deliberate departure from the 12–16px norm. Precision over friendliness |
| 2026-08-07 | Type scale has no sizes between 24 and 40 | Measured on Linear and Vercel; the gap is the hierarchy |
| 2026-08-07 | Dark-only for the hackathon, tokens light-ready | A second designed theme is ~1 day we do not have; demo and judging happen on one screen |
| 2026-08-07 | Mobile: engine pane becomes a bottom drawer | Decided now rather than at M7 so components are built drawer-aware from the first commit |
| 2026-08-07 | **Base UI over Radix + Vaul + Sonner + cmdk** | Vaul is unmaintained; the four-library mix has documented portal/focus conflicts. Base UI 1.6 provides Dialog, Drawer, Toast, and Combobox on one portal system |
| 2026-08-07 | No animated-component library (Aceternity, Magic UI, React Bits, Origin UI) | They are the primary source of the generic-AI-landing-page aesthetic, which is the stated thing to avoid |
| 2026-08-07 | No chart library; hand-rolled SVG | Two simple chart types needing exact token control; library defaults cost more to override than to write |
| 2026-08-07 | Landing page lives in the SPA as a lazy route | A separate static page paints faster but forks the design system into two places that drift |
| 2026-08-07 | The word "dashboard" is banned in code and copy | It invites the rejected wireframe-v2 mistake of demoting the conversation |
| 2026-08-07 | **Conversation text capped at 72ch** (§5.7) | Review finding: `minmax(560px, 1fr)` plus "extra width to the conversation" yields ~2000px lines on a 2560px monitor. The reviewer's screen is the widest this product is judged on |
| 2026-08-07 | **Guided first turn** (§9.1), not a passive empty state | ADR-13.4 means a judge's empty account *is* the live demo. A reviewer who does not know what to type never triggers the glass box at all |
| 2026-08-07 | **401 preserves the draft** (§6.11.1); no redirect, no modal | The never-lose-input guarantee (ADR-13.5) must reach the auth boundary. A redirect discards a typed message, which is the one thing this product promises never to do |
| 2026-08-07 | **Auth screens specified** (§6.17), left-weighted, not a centered card | Two of four routes had zero design. It is the only screen between the landing page and the product, and the default improvisation is a generic centered form |
| 2026-08-07 | **`uncited` renders as a quiet line** (§6.6), not a chip state | The backend emits a three-way status; only two had UI. Quiet because an uncited answer is a narrator weakness, not a system failure — but never hidden, or the validator has no teeth |
| 2026-08-07 | **Mobile keyboard + timeline rail specified** (§5.8) | The keyboard covering the composer is the classic mobile-chat failure; a 300-day rail at 390px is 1px bars. Both were unspecified |
| 2026-08-09 | **MoltenMetal (React Bits) added to the landing hero, breaking rules 6 and 12** | Explicit user instruction, given the trade-off (new `ogl` dependency, hardcoded hex colors, the exact "generic AI landing page" look §11 rejected React Bits for) and offered two on-brand alternatives first. Scoped narrowly: landing hero background only, lazy-loaded with the route so it does not touch the shared initial bundle, and skipped entirely under `prefers-reduced-motion` (rule 14 upheld even though upstream doesn't). Not a precedent for using React Bits elsewhere — §11's rejection stands everywhere else. |
| 2026-08-09 | **Staged progress (§6.10) is a connected-dot trail, not a self-overwriting line** | Explicit user instruction, referencing Claude Code's own tool-call trail as the reference. Every `event: stage` frame now appends to the pending turn instead of replacing it (`ChatTurn`'s `pending.stage?: string` → `pending.stages: string[]`); completed stages render as dimmed static dots on a `--border` connector, the current stage keeps the pulsing dot. Verified live against the real dev backend (screenshot: two-then-three stage trail rendering correctly, `retrieving…` → `assembling context…` → `generating…`). No new tokens: reuses `--signal` for the active dot exactly where the prior single-line version already used it (a pre-existing exception to rule 7, not a new one), `--border`/`--faint` for everything else. |
| 2026-08-09 | **Light theme added, superseding the original dark-only scope (rule 9, §5.2)** | Explicit user instruction. Literal colors now live in two mirrored `[data-theme]` blocks in `theme.css` instead of a single `:root`; every component reaches them through the same tokens as before (rule 6 unchanged), so this was zero component-level rewrites except a handful of call sites (below). `--signal` and `--invalid` are independently re-picked for light, not mirrored: dark's amber measured ~2.7:1 against a near-white background (well under the 4.5:1 body-text floor `text-signal` needs in `Receipt.tsx`), so both were darkened until they cleared AA against `--color-background`; `--color-foreground`/`muted-foreground`/`faint` verified the same "worst-case surface" way rule 7's original dark ramp was (see `theme.css`'s comments for the full contrast tables). `--color-background` itself was **not** a straight lightness-mirror of dark's near-black: the first pass (oklch 0.985, ~4% chroma of dark's) read as stark, generic "SaaS white" once seen running — flagged live, not by any contrast check, since contrast was never the problem — and was pulled down to 0.96 with chroma raised to match dark's own ramp, the whole surface stack shifted with it. Default follows `prefers-color-scheme`; an explicit toggle choice (`theme/themeStore.ts`, localStorage) wins over the OS setting from then on. `index.html` carries a small inline script duplicating the store's resolution order so the correct theme applies before first paint — an import from `main.tsx` cannot run early enough to prevent a flash. **The landing hero adapts too**, not a dark-only exception: `MoltenMetal`'s fragment shader turned out to alpha-composite normally (premultiplied, over a transparent clear) rather than additively, so unlike most WebGL "glow" backgrounds it isn't structurally locked to a dark canvas — `Landing.tsx` now picks a second `color1`/`color2`/`color3`/`blackPoint`/`brightness` set for light (inverting which end of the ramp reads as "hot": white-hot core on dark, deep-violet core on light, since white would vanish into a light page), keyed off `useTheme()`. That also let the header's scroll-aware forced-dark-over-the-hero hack (an earlier pass of this same change) get simpler: it no longer needs `data-theme="dark"` at all, just the transparent-over-hero / solid-once-scrolled surface swap, because the hero it floats over now genuinely matches the page's theme. Verified live in a real browser: dark and light, at the hero and scrolled, for the landing page, the app shell (empty + error states), auth, and 404. |
| 2026-08-09 | **Brand mark replaced everywhere, breaking rules 6 and 12** | Explicit user instruction, source provided as a design-tool export (`AyuMind Logo.dc.html` — ported into `Logo.tsx` and removed from `public/`; it was never meant to be served). Two counter-rotating gradient rings, an ECG trace that flows along its own path (`stroke-dashoffset`), a pulsing center node, and a slow heartbeat scale on the whole mark — hardcoded hex gradients and a glow filter, the same trade-off `MoltenMetal` already made (explicit instruction, scoped and documented) but wider: that one stays inside the landing hero, this one is the product's brand mark and replaces `Mark.tsx` (§2's old "filled square" glyph) everywhere it appeared — nav bars, chat avatars, auth, 404. Scoping happened along a different axis instead: `animated`/`glowStrength` default off for small, repeated, persistent chrome (nav icons, chat avatars — a long conversation renders this component many times over) and on only for prominent single placements (the landing hero's large mark). `useId()` scopes each instance's gradient/symbol ids so multiple instances on one page (guaranteed in the conversation) don't collide. Reduced motion is ANDed with `animated`, never bypassed by it (rule 14 upheld, same as every other animated component). The one thing NOT ported verbatim: the center "punch-through" circle was hardcoded `#04080b` in the source; made `var(--color-background)` instead so it actually matches the surface it sits on in both themes, added the same day. |
| 2026-08-09 | **Landing page pinned back to dark-only, amending the same-day light-theme work** | Explicit user instruction. `Landing.tsx`'s root now carries `data-theme="dark"` (plus `bg-background`, so `<body>` — outside that scope and still on the real site-wide theme — can't show through at the document's bottom edge under sub-pixel layout rounding), the same nesting mechanism `theme.css` documents for pinning any subtree regardless of its ancestors. This also let two things this same log already recorded as necessary get removed again: `MoltenMetal`'s light color variant (back to one hardcoded-dark palette, `useTheme` import gone) and the header's scroll-aware surface swap (back to always transparent/blurred — with the whole page dark by construction again, there's no second surface for it to disagree with, exactly the pre-light-theme reasoning). `ThemeToggle` is removed from the landing nav entirely rather than left inert: a toggle that visibly does nothing on the page you're looking at is a broken control, not a quiet one. The app, auth, and 404 are unaffected — this is landing-page-only. |
| 2026-08-09 | **A fifth empty state: the conversation for a returning account on a fresh thread** | Explicit user instruction, prompted by a screenshot of a real account (434 memories) showing a literal blank pane after "New chat" — `Conversation.tsx` rendered nothing at all when `turns.length === 0`, because that branch previously only existed to be skipped (`AppScreen`'s `isEmptyAccount` check routes genuinely empty accounts to `FirstRun` instead). Deliberately not a repeat of `FirstRun` — no example prompts, since this account already knows how to use the product — just `Logo` at 112px (animated; sized up from an initial 64px once seen running — small enough and singular enough on screen not to need the small/repeated-chrome calm treatment §16's Logo entry describes) plus the same two-line empty-state copy shape as every other row in §6.9's table. The one exception to that section's "never an illustration" rule, and documented as one there rather than silently. |
| 2026-08-09 | **The engine pane gained a collapse toggle** (§5.7) | Explicit user instruction. A handle centered on the column border, built from the existing `Button` (`secondary`/`icon`, no new component) rather than a bespoke pill — `radius-full` stays reserved for the avatar (rule 10). Positioned with plain `calc()` on `top`/`right` rather than the more idiomatic `top-1/2 -translate-y-1/2`/`translate-x-1/2` combination: that measurably failed to hit-test correctly live (`elementsFromPoint` reported the button on top; neither a real mouse click nor Playwright's own actionability check could reach it, while a direct `.click()` on the same element worked and correctly flipped React state) — a transform-vs-hit-testing mismatch worth recording since the `top-1/2`/`-translate-y-1/2` pairing is used without issue elsewhere in this codebase (e.g. `Timeline.tsx`'s "now" marker), so the failure mode is specific to this combination, not that pattern generally. Collapsing sets pane width to 0 (`overflow-hidden` clips its content during the transition); nothing flexes to claim the reclaimed space, keeping §5.7's "does not flex" rule intact for the pane itself. A second bug, also caught live (screenshots at 390px/834px): the desktop-only `hidden lg:flex` visibility classes were first applied directly to the `Button`, and did nothing — `Button`'s own base classes always include `inline-flex`, which conflicts with a consumer's `display` override since `cn` does not dedupe (Tailwind resolves same-property conflicts by generated-CSS order, not JSX order — the exact class of bug `Button.tsx`'s own `size="icon"` comment already warns about). The button rendered uncollapsed on every viewport below `lg`, on top of the empty state's "Ask something." text with no way to dismiss it. Fixed by moving `hidden`/`lg:block` (and the rest of the positioning) onto a wrapping `<div>` instead — the same pattern already used one JSX block up for the pane's own visibility, which never had this problem because it was never applied to a `Button`. Extended the same day to render on every breakpoint, not desktop-only: below `lg` it now toggles `isDrawerOpen` instead of `isPaneCollapsed`, sitting at the screen's right edge since there's no column border to center on. This isn't a new deviation — §5.8 already specified "the pane handle" as one of two ways to open the mobile drawer (alongside the citation gesture) and tablet as "collapsible," neither of which had a working control on any breakpoint until this button existed at all. Still open: §5.8's "defaults closed in portrait, open in landscape" for tablet — this button makes that state reachable, but nothing sets it as a default yet. |
| 2026-08-09 | **Mobile/tablet evidence drawer changed from a bottom sheet to a right-side sheet** (§5.8) | Explicit user instruction, after seeing the bottom sheet live and asking for it to "appear from right side, just like how on PC." Reverses the 2026-08-07 bottom-drawer decision on its own terms — that decision's stated reasoning (a half-height sheet is worse than either it or the keyboard alone) doesn't disqualify a right-side sheet, which doesn't compete with the keyboard for vertical space at all; §5.8's mobile-keyboard rules are amended in place to say so rather than left describing a drawer shape that no longer exists. Base UI's `Drawer` has no built-in side variant — `swipeDirection` only controls the swipe-to-dismiss gesture, not layout — so `Drawer.Popup` is positioned by hand exactly the way `EvidencePane`'s desktop column already is (`fixed`, full height, docked right, `border-l`, `w-pane` capped at `max-w-[88vw]` so it doesn't overflow a 360px phone). The top-of-sheet grab handle (`Drawer.SwipeArea`) is dropped entirely: it was a bottom-sheet-specific affordance with no equivalent on a side sheet, and the drawer already has two manual open/closes (the citation gesture and the same pane-handle button used on desktop) with nothing left needing a drag gesture to discover it. |
| 2026-08-09 | **Thread sidebar shipped, un-deferring §13's "multi-thread conversation UI"** | Explicit user instruction ("just like ChatGPT... I can load that chats again"). Full-stack: `GET /api/threads` (`engine/glassbox.py::fetch_threads`, `api/routers/glassbox.py::list_threads`) is new — `turns.thread_id`/`user_id`/`created_at`/`content` already had everything needed, no migration. Two auth-required, user-scoped tests added (`api/tests/test_glassbox.py`, matching the file's own `ROUTES` sweep pattern so the route can't be added without the cross-user isolation check running against it) — all 20 tests in the file pass. `preview` is the thread's *first* user message, verbatim, never an LLM-generated title: a summary would need computing and storing somewhere, and could drift from the conversation it labels: the first thing someone actually typed cannot drift from itself. `thread_id` in the response is de-namespaced (`user_id:client_id` → `client_id`) the same way `list_turns` already documents doing, so the client only ever deals in the same raw ids it already mints and sends to `POST /api/chat`. Frontend: `Sidebar`/`SidebarDrawer` mirror `EvidencePane`/`EvidenceDrawer`'s desktop-column/mobile-drawer split exactly, on the opposite edge — collapsible via the same `calc()`-positioned wrapper-`<div>`-around-`Button` pattern (§5.7's collapse-toggle entry), `PanelLeft*` icons instead of `PanelRight*`. The sidebar is a **peer of the top bar, full viewport height** (§5.7's wireframe redrawn as v4), not nested inside the content column — ChatGPT's own layout, and the reason `AppScreen`'s root gained one more level of flex nesting. Switching threads (`AppScreen.switchToThread`, shared by "New chat" and picking a thread from the list) resets `seeded.current` to `false`: the history-seeding effect is designed to run at most once per thread by that guard (re-seeding an *open* thread would drop its receipts), so loading a *different* thread's history back in requires deliberately re-arming it, not just changing `threadId`. Verified end-to-end against the real dev backend, not mocks: two real threads created via the real chat graph, both listed most-recent-first, clicking the older one correctly swaps the conversation with no cross-thread bleed — dark and light, desktop and mobile. |
| 2026-08-09 | **Thread sidebar moved below the top bar/timeline, amending wireframe v4's full-height column** (§5.7) | Explicit user instruction, after seeing the sidebar span the full viewport height above the timeline while the engine pane on the opposite edge started below it — the asymmetry read as unintentional. `AppScreen.tsx`'s root flips from a row (`sidebar` \| `top bar+timeline+content`) to a column (`top bar` → `timeline` → `row of sidebar+conversation+pane`): `TopBar` and `Timeline` are now direct children of the root, full width, and the sidebar column plus its collapse handle move into the same `relative flex min-h-0 flex-1` row that already held the conversation and the engine pane — the handle's `calc()` math is unchanged since it was already sized off the sidebar's own width variable, not the ancestor's. Mobile/tablet drawer behavior (§5.8) is unaffected; drawers were never scoped to "below the header" in the first place. **The top bar's own "New chat" button was removed in the same pass** — `ThreadList` (`Sidebar.tsx`) already renders one, and with the sidebar now visually adjacent to the conversation instead of a disconnected ChatGPT-style rail, having the same action in two places read as redundant rather than a deliberate belt-and-suspenders control. `TopBarProps.onNewChat` and its call site were deleted rather than left as unused dead code. |
| 2026-08-10 | **Custom scrollbar, token-driven, replacing default browser chrome everywhere** | Explicit user instruction ("three scrollbars... traditional browser scrollbar... improve the design, for both white and black theme"), covering the thread sidebar, the conversation column, and the engine pane at once rather than three one-off fixes. Added globally in `theme.css`'s `@layer base` — a universal `*` rule (`scrollbar-width: thin`, `scrollbar-color: var(--color-border-strong) transparent` for Firefox) plus `::-webkit-scrollbar*` pseudo-elements for Chromium/Safari (10px track, `--color-border-strong` thumb on a transparent track, `padding-box`-clipped 2px inset border so the thumb reads as inset rather than edge-to-edge, `--color-faint` on hover). No new tokens (rule 6 intact): both colors already existed for borders/muted text and simply carry over per-theme automatically, same as every other token consumer. `.scrollbar-none` (the timeline rail's fully-hidden scrollbar, unlayered) still wins over this by cascade-layer ordering, unchanged. |
| 2026-08-10 | **Staged progress (§6.10) simplified from a connected-dot trail to a single swapping line** | Explicit user instruction ("I dont want this connected dots vertically, simply in one line... remove that connected dots design"), reversing the 2026-08-09 decision on its own terms once seen running — that version's stacked `<ol>` of dimmed dots read as more machinery than the moment needed. `PendingTurn` (`Conversation.tsx`) now tracks only the latest stage (`stages[stages.length - 1]`) and swaps it via `AnimatePresence mode="wait"` (fade + 4px vertical slide, `exit` values collapsed to a no-op under reduced motion rather than passed as `undefined`, since `exactOptionalPropertyTypes` rejects an explicit `undefined` on a required prop) instead of appending to a growing list. The `--border`-connector and per-stage dimmed dots are gone; the single pulsing `--signal` dot now means "still working" rather than marking one specific stage. Landed together with two related, same-session changes to the same component: the pending-state `Logo` grew from 28px to 36px (reported live as "looking so small"), and the completed-turn `Logo` was removed outright — replaced by a 36px spacer `<div>` so the answer column doesn't reflow sideways when a turn settles from pending to complete — since a heartbeat/spin mark next to a *finished* answer was reading as decoration rather than "generating," the opposite of what the mark is for. |
| 2026-08-11 | **Profile/onboarding foundation approved, superseding §13's "onboarding tour" and "settings/profile screen" rejections** | Explicit user instruction, following an audit of the identity/account system (email+password only, no health profile) and the unused `user_profile` schema stub. §6.19 is the new component spec; ADR-17 ([09-decisions.md](docs/office-hours/09-decisions.md#adr-17)) is the backing architecture decision. Account creation itself is unchanged (email+password, no new required field) — the addition is a one-screen intake **after** signup and a small profile settings surface, both scoped to fields that feed a computed nutrition target or the agent's context, never a general settings page. Current weight remains a `weight` memory, never a mutable profile column (principle carried from the audit: one fact, one source of truth). Goal/target/preference changes write a `profile_change` memory alongside the `user_profile` row update, in the same transaction — the mechanism that keeps "you hit your target" honest against the target that was active on that historical day, not today's. |
| 2026-08-12 | **Today added as a second product surface (§6.20), amending §6.13's "no tab bar"** | Competitive research approved 2026-08-12, P0 #1. All five products studied (MyFitnessPal, Google Health, WHOOP, Oura, Apple Health) open to a Today-style home; AyuMind opened to a composer, so there was no way to answer "how am I doing?" without typing — and a judge with ninety seconds may never type. The nav count is now a stated ceiling of **two** rather than an accident of having only one screen: the same research found the median mature product runs three to five primary items and that Oura shipped a redesign *removing* two, so restraint here is the category's own lesson, not our limitation. Today deliberately keeps no machinery of its own — its composer hands off to `/app`'s existing turn pipeline and its rows open the timeline's existing day view — because a second send path or a second diary would fork the definitions of "a turn" and "a day" across two screens. |
| 2026-08-12 | **`value: null`, never `0`, for a metric with no logged rows — carried by an explicit `has_data` flag** | The screen's central honesty problem, and the reason the endpoint's shape is what it is. "You have logged nothing today" and "you ate 0 g of protein today" are different claims, and at 8 AM only the first is true. A JSON `0` makes them indistinguishable, and in the client `value === 0` and `value === null` are *both* falsy — so a plain truthiness check is exactly how a fabricated zero reaches the most-seen screen in the product. `has_data` exists so no component ever has to infer the difference. Pinned in both directions by `engine/tests/test_today.py`, including the case a naive fix would break: a meal that genuinely carried 0 g must still read as data. |
| 2026-08-12 | **Coverage shipped as a stated number; streaks rejected** | Three of five competitors ship streaks (MyFitnessPal's Streaks view, WHOOP's Streak, Google Health's badges) and it demonstrably works for them, so this was evaluated rather than assumed. It cannot work here: §1 defines this product *against* "rings, streaks and green checkmarks" and §3's anti-patterns ban green success checkmarks outright, so a streak flame would be the single most on-the-nose violation of the locked contract. But the need underneath one is real — *am I logging consistently enough for these averages to mean anything?* — and the engine already computes the honest answer as `coverage`, an input to `analytics.pattern_strength`. So it ships as a data-quality disclosure ("5 of the last 7 days logged"), not a reward. Same information, opposite framing, and the framing no competitor can copy without conceding their own reports don't disclose it. |
| 2026-08-12 | **`profile_change` excluded from Today's "Recently logged", alongside `insight`** | Found in the browser, not in review: `apply_profile_update` writes one memory per changed field, so a single onboarding submission filled four of the strip's eight rows with "activity level set to moderate" and buried a week of meals. `insight` was already excluded on the principle `Receipt` establishes — the user reported a meal, an insight is a claim the *engine* made — and a settings edit fails the same test from the other direction: it is a real memory and belongs in the profile history (ADR-17.1), but it is not a health event and it is not a useful way into a day of health data. The strip's job is receipts plus a browse affordance; both exclusions serve it. |
