/**
 * VitalsFigure — decorative torso/ECG mark for the right-hand side of `/login` and `/signup`
 * (§6.17 originally left that column to the graph rule alone; the space read as "completely
 * blank" once seen running).
 *
 * Ported from a design-tool export (`Neon Vitals Object.dc.html` + `assets/figure.png`, provided
 * directly and removed from `public/` once ported here — same precedent as `Logo.tsx`: the export
 * and its `support.js` templating runtime were never meant to be served as static assets). A
 * single spinning ring group (soft blur / mid blur / crisp, layered via SVG `<use>`), a pulsing
 * radial halo, a heartbeat-shaped trace whose bright dot sweeps the path on a loop, and a slow
 * whole-mark drift — all built around the source raster figure (`vitals-figure.png`), which cannot
 * be recolored. The source export's fourth element, a blurred ellipse standing in for a ground
 * shadow beneath the figure, was dropped: at this element's actual `stdDeviation` it read as a
 * flat glowing bar/rectangle rather than a soft glow once seen against this screen's plain
 * background (the source's own dark radial-gradient backdrop, not reused here, had hidden that
 * same edge). DESIGN.md's own restraint rule (no shadows outside `--shadow-overlay`/
 * `--shadow-popover`) argues for cutting it rather than re-tuning it.
 *
 * **Recolored 2026-08-16** (explicit user instruction, after the auth screens read as "not the
 * same website" as the landing page): the ring, ECG trace, and halo now draw from `Logo.tsx`'s own
 * three gradients (`mainId`/`coolId`/`warmId` — same hex stops, copied rather than re-derived) in
 * place of a single flat `#6fe8dc` this component picked up from the source export's own cyan
 * figure. The raster figure itself still can't be recolored, so its `drop-shadow` tint was moved
 * to `#1fc9a6` (the brand ramp's own middle stop) so the glow around it at least reads as the same
 * hue family the ring is now drawn in, rather than a third, unrelated cyan.
 *
 * **This breaks rules 6 and 12 on purpose** (hardcoded hex colors, no gradients), the same
 * documented trade-off `Logo.tsx` and `MoltenMetal.tsx` already made — see DESIGN.md §16 Decisions
 * Log. Purely decorative (`aria-hidden`, no accessible name): the auth form itself carries every
 * piece of information on this screen.
 *
 * **Reduced motion is not negotiable regardless of the `animated` prop** (rule 14), same
 * `animated && !reduce` AND as `Logo.tsx` — a paused animation renders its 0%/`from` keyframe, so
 * the reduced path is a static mark, never a broken or half-drawn one.
 */

import { useId } from "react";
import { useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import figureUrl from "@/assets/vitals-figure.png";
import "./VitalsFigure.css";

export interface VitalsFigureProps {
  className?: string;
  /** Independent of `prefers-reduced-motion`, which always wins when set (see module doc). */
  animated?: boolean;
}

export function VitalsFigure({ className, animated = true }: VitalsFigureProps) {
  const reduce = useReducedMotion();
  const isAnimated = animated && !reduce;
  const playState = isAnimated ? "running" : "paused";

  const uid = useId().replace(/:/g, "");
  const softId = `vf-soft-${uid}`;
  const midId = `vf-mid-${uid}`;
  const haloId = `vf-halo-${uid}`;
  const ringId = `vf-ring-${uid}`;
  const mainId = `vf-main-${uid}`;
  const coolId = `vf-cool-${uid}`;
  const warmId = `vf-warm-${uid}`;

  const ecgPath = "M146 282 H182 L188 274 L193 282 H201 L207 252 L215 320 L222 262 L228 282 H239 L244 276 L249 282 H254";

  return (
    <div
      aria-hidden="true"
      className={cn("vf-root relative aspect-square", className)}
      style={{ animationPlayState: playState }}
    >
      <svg
        viewBox="0 0 400 400"
        className="absolute inset-0 block size-full overflow-visible"
        fill="none"
      >
        <defs>
          <filter id={softId} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="7" />
          </filter>
          <filter id={midId} x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.4" />
          </filter>
          {/* Same three stops as Logo.tsx's mainId/coolId/warmId — copied, not re-derived, so this
              mark's ring and ECG trace read as the brand mark's own palette rather than a fourth,
              unrelated hue introduced just for this screen. */}
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
          <radialGradient id={haloId} cx="50%" cy="52%" r="50%">
            <stop offset="0%" stopColor="#22c9c4" stopOpacity="0.28" />
            <stop offset="55%" stopColor="#1490d4" stopOpacity="0.1" />
            <stop offset="100%" stopColor="#0a1017" stopOpacity="0" />
          </radialGradient>
          <g id={ringId}>
            <circle
              cx="200"
              cy="200"
              r="150"
              stroke={`url(#${coolId})`}
              strokeWidth="1.9"
              fill="none"
              strokeDasharray="245 28.3 179.1 28.3 226.2 28.3 179.1 28.3"
              strokeLinecap="round"
            />
            <circle cx="176.5" cy="348" r="4.6" fill={`url(#${warmId})`} />
            <circle cx="50.1" cy="204.7" r="4.6" fill={`url(#${coolId})`} />
            <circle cx="214.1" cy="50.6" r="4.6" fill={`url(#${warmId})`} />
            <circle cx="349.4" cy="185.9" r="4.6" fill={`url(#${coolId})`} />
          </g>
        </defs>

        <ellipse
          cx="200"
          cy="205"
          rx="150"
          ry="150"
          fill={`url(#${haloId})`}
          className="vf-halo"
          style={{ animationPlayState: playState }}
        />

        <circle cx="200" cy="200" r="150" stroke="#22c9c4" strokeWidth="1.1" fill="none" opacity="0.13" />

        <g className="vf-spin" style={{ animationPlayState: playState }}>
          <use href={`#${ringId}`} filter={`url(#${softId})`} opacity="0.55" />
          <use href={`#${ringId}`} filter={`url(#${midId})`} opacity="0.5" />
          <use href={`#${ringId}`} />
        </g>
      </svg>

      <img
        src={figureUrl}
        alt=""
        className="absolute"
        style={{
          left: "20%",
          top: "21%",
          width: "60%",
          height: "67%",
          filter: "saturate(1.06) drop-shadow(0 0 14px rgba(31, 201, 166, 0.35))",
        }}
      />

      <svg viewBox="0 0 400 400" className="absolute inset-0 block size-full overflow-visible" fill="none">
        <g transform="translate(200 250) scale(0.9) translate(-200 -282)">
          <path
            d={ecgPath}
            stroke={`url(#${mainId})`}
            strokeWidth="5"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
            filter={`url(#${softId})`}
          />
          <path
            d={ecgPath}
            stroke="#b9ffd0"
            strokeWidth="1.9"
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
          <circle
            r="3.4"
            fill="#ffffff"
            className="vf-sweep"
            style={{
              offsetPath: `path('${ecgPath}')`,
              offsetRotate: "0deg",
              filter: "drop-shadow(0 0 6px #22c9c4) drop-shadow(0 0 14px #4ade80)",
              animationPlayState: playState,
            }}
          />
        </g>
      </svg>
    </div>
  );
}
