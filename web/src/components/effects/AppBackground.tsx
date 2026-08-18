/**
 * The landing hero's shader, reused as the ambient backdrop for the whole app shell —
 * Chat, Review and Profile (§16 Decisions Log, 2026-08-18; explicit instruction).
 *
 * **This extends an existing deviation rather than opening a new one.** `MoltenMetal` is already
 * a recorded departure from §11/§12 (hardcoded hex, shader-driven glow, React Bits) kept on
 * instruction for the hero, 2026-08-08. What is new here is *where* it renders: behind product
 * surfaces rather than behind a marketing headline, which is the case §4.4's "never behind body
 * copy" rule was written about. Two things keep that honest:
 *
 *   1. **The scrim.** A `--background` plate at {@link SCRIM_ALPHA} sits between the shader and
 *      every screen, so what reaches the reader is an ambient tint, not a moving image competing
 *      with 13px evidence rows. Text contrast stays computed against the same token it always
 *      was, because the plate is that token.
 *   2. **Dimmed and slowed.** Half the hero's speed and {@link SHADER_OPACITY} of its opacity.
 *      The hero is the first five seconds of a marketing page; this is a surface people read for
 *      minutes.
 *
 * Both dials are the two constants below, deliberately at the top of the file: tuning the effect
 * should never mean editing JSX.
 *
 * Rendered once by App.tsx's layout route rather than per screen, so navigating Chat → Review →
 * Profile keeps ONE WebGL context alive instead of tearing one down and initializing another on
 * every navigation (a visible re-seed of the animation, and the expensive half of this effect).
 *
 * Reduced motion: `MoltenMetal` starts no animation loop and renders nothing at all, so this
 * degrades to the scrim over `<body>`'s own `--background` — exactly the app as it looked before.
 * Nothing is lost but the decoration, which is rule 14's requirement.
 */

import MoltenMetal from "@/components/effects/MoltenMetal";

/** How much of the shader survives the scrim. Lower = calmer. */
const SHADER_OPACITY = 0.55;
/** Opacity of the `--background` plate over the shader. Higher = more readable, less visible
 * effect. Tuned so dense mono type on the conversation column keeps its token contrast. */
const SCRIM_ALPHA = "88";

export function AppBackground() {
  return (
    // `fixed` + `-z-10`: it must not scroll with Review's or Profile's long pages, and it must
    // sit behind every screen's content without any of them needing to know it exists. The app
    // shells paint no opaque background of their own (that is the change this component asks of
    // them) — `<body>` still carries `--background`, so this layer composites over the same
    // color the app has always used rather than over nothing.
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <MoltenMetal
        color1="#5227FF"
        color2="#FF9FFC"
        color3="#FFFFFF"
        // Half the hero's 0.35: at full speed the movement is legible in peripheral vision while
        // reading, which is precisely what a backdrop must not be.
        speed={0.17}
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
        // The hero's mouse warp is an invitation to play with a marketing page. Here the cursor
        // belongs to the composer, the citations and the timeline — a background that reacts to
        // it would read as a second, competing interaction.
        mouseInteraction={false}
        opacity={SHADER_OPACITY}
      />
      <div className="absolute inset-0 bg-background" style={{ opacity: `0.${SCRIM_ALPHA}` }} />
    </div>
  );
}
