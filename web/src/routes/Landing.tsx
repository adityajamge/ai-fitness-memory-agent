/**
 * Landing page — DESIGN.md §8.
 *
 * The thesis: most SaaS landing pages *describe* a product; this one **runs** it. The most
 * persuasive asset available is a real cited answer resolving to real database rows, and it beats
 * any gradient mesh or feature grid we could build.
 *
 * Constraint, stated plainly: ADR-13.7 locks us to a Vite SPA served as static assets, so we
 * cannot match a CDN-backed SSG marketing site's first paint. We match their **craft** instead —
 * typographic contrast, restraint, real content, and motion that responds to input rather than
 * playing on load.
 *
 * Deliberately absent, per §3 and §8.3: testimonials (we have none, and inventing them is
 * disqualifying), a logo wall, pricing, a three-column icon grid, and any gradient.
 */

import { m, useReducedMotion } from "motion/react";
import { Link } from "react-router";
import MoltenMetal from "@/components/effects/MoltenMetal";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

/** Scroll-triggered reveal. Fires once and never re-triggers on scroll-up (§8.5) — a section that
 * re-animates every time it passes the viewport is the tell of an unconsidered page. */
function Reveal({
  children,
  className,
  delay = 0,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const reduce = useReducedMotion();
  return (
    <m.div
      initial={reduce ? false : { opacity: 0, y: 12 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.15 }}
      transition={{ duration: reduce ? 0 : 0.32, delay: reduce ? 0 : delay, ease: [0.2, 0, 0, 1] }}
      className={className}
    >
      {children}
    </m.div>
  );
}

/** A citation chip, rendered exactly as it is in the app. The consistency is the point: a chip
 * that behaves the same on the marketing page and inside the product is itself a quality signal. */
function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="mx-0.5 inline-flex items-baseline gap-1 rounded-xs border border-signal/40 bg-signal-dim px-1.5 py-0.5 align-baseline">
      <span className="font-mono text-meta text-foreground">{children}</span>
    </span>
  );
}

function Section({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("mx-auto w-full max-w-marketing px-6 py-16 md:py-24", className)}>
      {children}
    </section>
  );
}

export function Landing() {
  const reduce = useReducedMotion();

  return (
    // Pinned dark regardless of the site-wide theme toggle (§16 Decisions Log, 2026-08-09,
    // amending the light-theme work from earlier the same day): explicit instruction — the
    // landing page keeps MoltenMetal's hardcoded-dark hero and everything below it in that same
    // dark register, rather than the rest of the page adapting to light. `[data-theme]` is
    // theme.css's own nesting mechanism (see its comments), the same trick that used to pin only
    // the hero before this page went back to dark-only wholesale. `bg-background` here too, not
    // just on individual sections: `<body>` sits outside this div and stays on the real
    // site-wide theme, so without this a light-themed visitor could see a hairline of `<body>`'s
    // light background at the document's bottom edge (sub-pixel layout rounding).
    <div data-theme="dark" className="min-h-dvh bg-background">
      {/* `fixed`, not `sticky`: it needs to visually float over the hero's shader background
          from y=0, which a sticky element (in normal flow, pushed below the hero) cannot do.
          Transparent + blurred rather than a solid bar, so the animated background reads
          *through* it — every section below is dark by construction now, so legibility holds
          once scrolled past the hero too; there is no second surface for it to disagree with. */}
      <header className="fixed inset-x-0 top-0 z-30 border-b border-transparent backdrop-blur transition-colors duration-240">
        <nav className="mx-auto flex h-14 w-full max-w-marketing items-center gap-3 px-6">
          <Logo size={36} animated={false} glowStrength={0} />
          <span className="text-dense font-medium text-foreground">AyuMind AI</span>
          <div className="ml-auto flex items-center gap-2">
            <Link to="/login">
              <Button variant="ghost" size="sm">Sign in</Button>
            </Link>
            <Link to="/signup">
              <Button variant="primary" size="sm">Try it</Button>
            </Link>
          </div>
        </nav>
      </header>

      {/* ── Hero (§8.2). Left-weighted, full-bleed, no centered composition. `bg-background` is
          load-bearing, not decorative: under `prefers-reduced-motion` MoltenMetal renders nothing
          at all (by design — see its own docstring), so this dark backdrop is what shows
          instead. */}
      <div className="relative overflow-hidden bg-background">
        {/* Deliberate DESIGN.md deviation — see MoltenMetal.tsx's docstring and §16 Decisions
            Log. Skipped entirely under prefers-reduced-motion (handled inside the component).
            Spans from y=0 (the actual top of the page, behind the now-transparent fixed header)
            rather than starting below it. */}
        <div aria-hidden="true" className="pointer-events-none absolute inset-0">
          <MoltenMetal
            color1="#5227FF"
            color2="#FF9FFC"
            color3="#FFFFFF"
            speed={0.35}
            scale={4}
            detail={3}
            glow={1.6}
            coreSize={0.1}
            swirl={1}
            fold={-0.2}
            blackPoint={0.05}
            brightness={1.3}
            colorMode="molten"
            grain
            grainIntensity={0.05}
            mouseInteraction
            mouseStrength={0.3}
            opacity={1}
          />
        </div>
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 [background-image:var(--graph-rule)] [mask-image:linear-gradient(to_bottom,black,transparent_75%)]"
        />

        {/* Reserves the fixed header's height so hero content starts below it instead of under
            it — the background layers above are unaffected and still reach all the way to y=0. */}
        <div aria-hidden="true" className="h-14" />

        <Section className="relative py-24 md:py-32">
          <div className="grid gap-12 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              {/* No `ch` cap on this container: `ch` resolves against the *container's* 15px
                  font, which would wrap the 72px heading and orphan the last word onto its own
                  line. The heading is two short lines by construction and needs no measure
                  limit. */}
              <h1 className="text-display-s text-foreground md:text-display-m lg:text-display-l">
                It remembers.
                <br />
                {/* The claim is bright, the proof-of-claim is quiet. That one tonal shift is the
                    whole brand in two lines. */}
                <span className="text-muted-foreground">And it can prove it.</span>
              </h1>

              <p className="mt-8 max-w-[52ch] text-lead text-muted-foreground">
                A health companion with a real memory: every meal, workout, and scan becomes a
                queryable row. Ask what changed, and get the receipts.
              </p>

              <div className="mt-10 flex flex-wrap items-center gap-3">
                <Link to="/signup">
                  <Button variant="primary" size="lg">Try it</Button>
                </Link>
                <a href="#proof">
                  <Button variant="secondary" size="lg">See the glass box ↓</Button>
                </a>
              </div>
            </div>

            {/* Right-side mark — decorative only (`Logo` defaults to `aria-hidden`; the nav's
                visible "AyuMind AI" text already carries the name), so hiding it below `lg` costs
                nothing and keeps a tight viewport uncluttered. `Logo`'s own animation (rings, ECG
                flow, heartbeat scale) already honors reduced motion; this wrapper's *added* float
                is the one extra motion layer that needs its own opt-out. */}
            <m.div
              className="hidden justify-self-center lg:block"
              {...(!reduce && {
                animate: { y: [0, -18, 0], rotate: [-3, 3, -3] },
                transition: { duration: 7, repeat: Infinity, ease: "easeInOut" as const },
              })}
            >
              <Logo size={288} className="h-56 w-56 xl:h-72 xl:w-72" />
            </m.div>
          </div>

          {/* The hero visual IS the product: a real cited answer, not a screenshot of one. */}
          <Reveal delay={0.15} className="mt-16">
            <div className="max-w-[720px] rounded-lg border border-border bg-surface p-5 md:p-6">
              <p className="max-w-[72ch] text-body text-foreground">
                Your body fat began falling around <Chip>Jun 2 · scan 21.4%</Chip>. Protein rose
                ~96→142 g/day from <Chip>May 12</Chip>, and sleep crossed 7.5 h/night after{" "}
                <Chip>May 19</Chip>.
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-border pt-4 text-meta text-muted-foreground">
                <span className="text-signal">✦</span>
                <span className="font-mono">3 citations</span>
                <span aria-hidden="true" className="text-faint">·</span>
                <span className="font-mono">44 memories in evidence set</span>
                <span aria-hidden="true" className="text-faint">·</span>
                <span className="font-mono text-faint">2 queries</span>
              </div>
            </div>
          </Reveal>
        </Section>
      </div>

      {/* ── §2 The forgetting ───────────────────────────────────────────────────────────── */}
      <Section>
        <Reveal>
          <h2 className="max-w-[20ch] text-display-s text-foreground">
            Most assistants meet you for the first time, every time.
          </h2>
        </Reveal>
        <div className="mt-10 grid gap-4 md:grid-cols-2">
          <Reveal>
            <div className="h-full rounded-md border border-border bg-surface p-5">
              <p className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                a stateless assistant
              </p>
              <p className="mt-3 text-body text-muted-foreground">
                "I don't have access to your previous conversations, so I can't tell you what you
                ate in June."
              </p>
            </div>
          </Reveal>
          <Reveal delay={0.08}>
            <div className="h-full rounded-md border border-signal/30 bg-surface p-5">
              <p className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                this one
              </p>
              <p className="mt-3 text-body text-foreground">
                "You averaged <span className="font-mono">142 g</span> protein/day across{" "}
                <span className="font-mono">28</span> logged days in June."
              </p>
            </div>
          </Reveal>
        </div>
      </Section>

      {/* ── §3 The money shot ───────────────────────────────────────────────────────────── */}
      <div id="proof" className="border-y border-border bg-surface/40">
        <Section>
          <Reveal>
            <h2 className="max-w-[24ch] text-display-s text-foreground">
              Ask what changed. Get the rows that prove it.
            </h2>
            <p className="mt-5 max-w-[60ch] text-lead text-muted-foreground">
              Every factual claim is a chip. Click one and the evidence pane shows the memory it
              resolves to, with its provenance, its confidence, and the query that found it.
            </p>
          </Reveal>

          <Reveal delay={0.1} className="mt-10">
            <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
              <div className="rounded-md border border-border bg-surface p-5">
                <p className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                  the question
                </p>
                <p className="mt-3 max-w-[72ch] text-body text-foreground">
                  What changed before my body fat started dropping?
                </p>
                <div className="mt-5 border-t border-border pt-5">
                  <pre className="overflow-x-auto font-mono text-meta text-muted-foreground">
                    <code>{`SELECT date_trunc('week', event_time) AS wk,
       avg((payload->'nutrition'->>'protein_g')::float)
FROM memories
WHERE user_id = $1 AND type = 'meal'
GROUP BY 1 ORDER BY 1;`}</code>
                  </pre>
                </div>
              </div>

              <div className="rounded-md border border-border bg-surface p-4">
                <p className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                  evidence
                </p>
                <ul className="mt-3 flex flex-col gap-2">
                  {[
                    { s: "Body scan 21.4% BF", d: "Jun 2", p: "live", c: 4 },
                    { s: "Body scan 23.1% BF", d: "May 3", p: "reconstructed", c: 3 },
                    { s: "Meal 46g protein", d: "May 12", p: "reconstructed", c: 4 },
                  ].map((row) => (
                    <li
                      key={row.s}
                      className="rounded-md border border-border bg-surface-2 px-3 py-2.5"
                    >
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="text-dense text-foreground">{row.s}</span>
                        <span className="font-mono text-meta text-muted-foreground">{row.d}</span>
                      </div>
                      <div className="mt-2 flex items-center gap-2">
                        <span
                          className={cn(
                            "rounded-xs px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.08em]",
                            row.p === "live"
                              ? "bg-surface-3 text-muted-foreground"
                              : "border border-dashed border-border text-faint",
                          )}
                        >
                          {row.p}
                        </span>
                        <span className="inline-flex gap-px">
                          {[0, 1, 2, 3].map((i) => (
                            <span
                              key={i}
                              className={cn(
                                "h-2.5 w-[3px] rounded-xs",
                                i < row.c ? "bg-muted-foreground" : "bg-surface-3",
                              )}
                            />
                          ))}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Reveal>
        </Section>
      </div>

      {/* ── §5 Why not a vector store ───────────────────────────────────────────────────── */}
      <Section>
        <Reveal>
          <h2 className="max-w-[24ch] text-display-s text-foreground">
            Some memories you retrieve. Some you have to compute.
          </h2>
        </Reveal>
        <div className="mt-10 flex flex-col gap-4">
          <Reveal>
            <div className="flex flex-col gap-2 rounded-md border border-border bg-surface p-5 md:flex-row md:items-center md:gap-8">
              <p className="min-w-0 flex-1 font-mono text-dense text-foreground">
                "when did I last complain about my knee?"
              </p>
              <p className="text-meta text-muted-foreground">
                vector search over embeddings — a similarity problem
              </p>
            </div>
          </Reveal>
          <Reveal delay={0.08}>
            <div className="flex flex-col gap-2 rounded-md border border-signal/30 bg-surface p-5 md:flex-row md:items-center md:gap-8">
              <p className="min-w-0 flex-1 font-mono text-dense text-foreground">
                "protein in June"
              </p>
              <p className="text-meta text-muted-foreground">
                <span className="font-mono text-foreground">SUM … GROUP BY week</span> — vector
                search cannot do this at all
              </p>
            </div>
          </Reveal>
        </div>
      </Section>

      {/* ── §6 Honest by construction ───────────────────────────────────────────────────── */}
      <Section>
        <Reveal>
          <h2 className="max-w-[24ch] text-display-s text-foreground">
            It tells you when it is not sure.
          </h2>
          <p className="mt-5 max-w-[60ch] text-lead text-muted-foreground">
            Confidence, provenance, and retraction are shown, not hidden. A memory rebuilt from
            records is marked as rebuilt. An insight that stops holding is retracted, and the
            retraction stays visible.
          </p>
        </Reveal>
      </Section>

      {/* ── §7 CTA ──────────────────────────────────────────────────────────────────────── */}
      <div className="border-t border-border">
        <Section className="py-20 md:py-28">
          <Reveal>
            <h2 className="max-w-[18ch] text-display-s text-foreground">
              Start your memory.
            </h2>
            <div className="mt-8">
              <Link to="/signup">
                <Button variant="primary" size="lg">Create an account</Button>
              </Link>
            </div>
            {/* Sets the ADR-13.4 expectation up front and turns a limitation into a statement of
                integrity. */}
            <p className="mt-6 font-mono text-meta text-faint">
              every account starts empty — including yours
            </p>
          </Reveal>
        </Section>
      </div>

      <footer className="border-t border-border">
        <div className="mx-auto flex w-full max-w-marketing flex-wrap items-center gap-3 px-6 py-8">
          <Logo size={20} animated={false} glowStrength={0} aria-label="AyuMind AI" />
          <span className="text-meta text-faint">Your memory, structured and provable.</span>
          <a
            href="https://github.com/adityajamge/ai-fitness-memory-agent"
            className="ml-auto text-meta text-muted-foreground underline decoration-border underline-offset-4 transition-colors duration-120 hover:decoration-foreground"
          >
            Source
          </a>
        </div>
      </footer>
    </div>
  );
}
