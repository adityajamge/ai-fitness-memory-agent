/**
 * The brand mark — DESIGN.md §2 (replaced 2026-08-09, see §16 Decisions Log).
 *
 * Ported from a design-tool export (`AyuMind Logo.dc.html`, provided directly and removed from
 * `public/` once ported here — it was never meant to be served as a static asset, and its
 * `support.js`/templating aren't part of this app). The visual: two counter-rotating rings, an
 * ECG trace that pulses along its own path, a glowing center node, and a slow heartbeat scale on
 * the whole mark.
 *
 * **This breaks rules 6 and 12 on purpose** (hardcoded hex colors and gradients) — same trade-off
 * this codebase already made once for `MoltenMetal`, on the same basis: explicit instruction,
 * scoped and documented rather than silently ignored. Unlike `MoltenMetal`, this one is NOT
 * scoped to a single hero — it replaces the brand mark everywhere, which is the whole point of a
 * "common Logo component."
 *
 * **Every instance is self-contained.** `<symbol>`/gradient ids from the source export would
 * collide if this component ever renders twice on one page (it does — one per chat turn), so
 * `useId()` scopes them per instance.
 *
 * **Reduced motion is not negotiable regardless of the `animated` prop** (rule 14): both are
 * ANDed together into `isAnimated`, which drives every `animationPlayState` below. A `paused`
 * animation that never started simply renders its 0%/`from` keyframe, so the reduced path is a
 * normal static mark, not a broken or half-drawn one.
 */

import { useId } from "react";
import { useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import "./Logo.css";

export interface LogoProps {
  /** Pixel size, square. */
  size?: number;
  /** Rings spin, the ECG trace flows, the node pulses, the whole mark keeps a slow heartbeat.
   * Independent of `prefers-reduced-motion`, which always wins when set (see module doc). */
  animated?: boolean;
  /** 0 disables the glow entirely — the right call for small/repeated instances (nav icons, chat
   * avatars), where a 30px blur dwarfs a 20px mark and multiplies per instance down a long
   * conversation. 1 is the source asset's own default, for prominent single placements. */
  glowStrength?: number;
  className?: string;
  /** Omit for a purely decorative mark (the default — matches the old `Mark` component's
   * convention of relying on adjacent visible text or a wrapping link's own label). Pass a
   * string only where this is the sole accessible identification of the brand in context, e.g.
   * the landing footer's mark, which sits next to a tagline rather than the word "AyuMind AI". */
  "aria-label"?: string;
}

export function Logo({
  size = 24,
  animated = true,
  glowStrength = 1,
  className,
  "aria-label": ariaLabel,
}: LogoProps) {
  const reduce = useReducedMotion();
  const isAnimated = animated && !reduce;
  const playState = isAnimated ? "running" : "paused";

  const uid = useId().replace(/:/g, "");
  const mainId = `logo-main-${uid}`;
  const coolId = `logo-cool-${uid}`;
  const warmId = `logo-warm-${uid}`;

  const glowFilter =
    glowStrength > 0
      ? `drop-shadow(0 0 ${10 * glowStrength}px rgba(46,224,192,${0.28 * glowStrength})) ` +
        `drop-shadow(0 0 ${30 * glowStrength}px rgba(30,157,224,${0.2 * glowStrength}))`
      : undefined;

  return (
    <svg
      viewBox="0 0 240 240"
      width={size}
      height={size}
      className={cn("shrink-0", className)}
      role={ariaLabel ? "img" : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      style={{
        transformOrigin: "50% 50%",
        animation: "logo-beat 2.6s ease-in-out infinite",
        animationPlayState: playState,
        filter: glowFilter,
      }}
    >
      <defs>
        <linearGradient id={mainId} x1="0.1" y1="1" x2="0.9" y2="0">
          <stop offset="0%" stopColor="#0fa3cf" />
          <stop offset="50%" stopColor="#1fc9a6" />
          <stop offset="100%" stopColor="#4ade80" />
        </linearGradient>
        <linearGradient id={coolId} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1490d4" />
          <stop offset="100%" stopColor="#22c9c4" />
        </linearGradient>
        <linearGradient id={warmId} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#1fc9a6" />
          <stop offset="100%" stopColor="#6ef08e" />
        </linearGradient>
      </defs>

      <g
        style={{
          transformOrigin: "120px 120px",
          animation: "logo-spin 30s linear infinite",
          animationPlayState: playState,
        }}
      >
        <circle
          cx="120"
          cy="120"
          r="78"
          fill="none"
          stroke={`url(#${coolId})`}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray="150 60 90 60"
        />
      </g>
      <g
        style={{
          transformOrigin: "120px 120px",
          animation: "logo-spin-back 20s linear infinite",
          animationPlayState: playState,
        }}
      >
        <circle
          cx="120"
          cy="120"
          r="54"
          fill="none"
          stroke={`url(#${warmId})`}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray="110 42 58 42"
        />
      </g>

      {/* The ECG trace: a faint full path underneath, and a bright short dash animated along it
          on top — the "flow" is `stroke-dashoffset` marching around the same `d`, not two
          different paths. */}
      <path
        d="M 46 120 L 86 120 L 95 100 L 106 152 L 118 96 L 128 120 L 194 120"
        fill="none"
        stroke={`url(#${mainId})`}
        strokeWidth="7"
        strokeOpacity="0.35"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M 46 120 L 86 120 L 95 100 L 106 152 L 118 96 L 128 120 L 194 120"
        fill="none"
        stroke="#b9ffd0"
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeDasharray="48 216"
        style={{ animation: "logo-ecg-flow 2.6s linear infinite", animationPlayState: playState }}
      />

      {/* Theme-aware, unlike the source export's hardcoded `#04080b`: this is a punch-through so
          the rings and ECG trace appear to pass behind the center node, and it has to match
          whatever surface the mark is actually sitting on in either theme. */}
      <circle cx="120" cy="120" r="16" fill="var(--color-background)" />
      <circle
        cx="120"
        cy="120"
        r="10"
        fill={`url(#${warmId})`}
        style={{
          transformOrigin: "120px 120px",
          animation: "logo-node-glow 2.6s ease-in-out infinite",
          animationPlayState: playState,
        }}
      />
    </svg>
  );
}
