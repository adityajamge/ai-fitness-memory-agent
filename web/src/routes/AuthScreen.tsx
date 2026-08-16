/**
 * Login and signup — DESIGN.md §6.17 (F-T2).
 *
 * One component, two modes. They differ by four strings and one error path; forking them into two
 * files would guarantee they drift, and this is the only screen between the landing page and the
 * product.
 *
 * **Left-weighted, not a centered card.** No `--surface` panel, no border, no shadow: the form
 * sits directly on the page. Centering it would be the most conventional choice available, and
 * §3's anti-pattern list rules out centered-everything precisely because it is the default that
 * makes considered products look generic. Left-weighting matches the hero, so signup reads as the
 * same product rather than a bolted-on auth page.
 */

import { useState, type FormEvent } from "react";
import { Eye, EyeOff } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { ApiError, login as loginRequest, signup as signupRequest } from "@/api/client";
import Particles from "@/components/effects/Particles";
import { VitalsFigure } from "@/components/effects/VitalsFigure";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";

const COPY = {
  login: {
    title: "Welcome back",
    submit: "Sign in",
    switchText: "New here?",
    switchLabel: "Create an account",
    switchTo: "/signup",
  },
  signup: {
    title: "Start your memory",
    submit: "Create account",
    switchText: "Already have an account?",
    switchLabel: "Sign in",
    switchTo: "/login",
  },
} as const;

export function AuthScreen({ mode }: { mode: "login" | "signup" }) {
  const copy = COPY[mode];
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [passwordTouched, setPasswordTouched] = useState(false);
  const [emailError, setEmailError] = useState<string>();
  const [passwordError, setPasswordError] = useState<string>();
  const [formError, setFormError] = useState<string>();
  const [isSubmitting, setSubmitting] = useState(false);

  // Live, but only after the field has been left once. Validating while someone is still typing
  // their eighth character is nagging, not helping.
  const passwordTooShort =
    mode === "signup" && passwordTouched && password.length > 0 && password.length < 8;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setEmailError(undefined);
    setPasswordError(undefined);
    setFormError(undefined);

    if (mode === "signup" && password.length < 8) {
      setPasswordTouched(true);
      setPasswordError("at least 8 characters");
      return;
    }

    setSubmitting(true);
    try {
      await (mode === "signup" ? signupRequest : loginRequest)(email, password);
      // Signup routes through the one-time intake screen (DESIGN.md §6.19, ADR-17) and on into
      // the guided first turn — onboarding is a signup follow-up, not a gate re-enforced on
      // every session, and "Skip for now" means skipping stays skipped.
      //
      // Login lands on **Chat**, same as signup → onboarding does (2026-08-13 IA revision,
      // §6.13): "talk to your health memory" is the core promise, so Chat is home for every
      // account, not a briefing. The asymmetry that used to exist here (returning accounts to
      // Today, new accounts to Chat) is gone — both paths land on the same surface now.
      navigate(mode === "signup" ? "/onboarding" : "/app", { replace: true });
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 409) {
          setEmailError("that email already has an account");
        } else if (error.status === 401) {
          // Deliberately does not say which field was wrong. Naming it confirms an account
          // exists to anyone probing for valid addresses.
          setPasswordError("email or password is incorrect");
        } else if (error.status === 422) {
          setEmailError("that doesn't look like an email address");
        } else {
          setFormError("couldn't reach the server");
        }
      } else {
        setFormError("couldn't reach the server");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="relative min-h-dvh overflow-hidden">
      {/* A slow drifting starfield across the full page (explicit user instruction — confining it
          to the right column, the first pass here, read as the field stopping short rather than
          as a deliberate composition). `pointer-events-none` throughout, so it never blocks the
          form; white points rather than the brand cyan, also explicit instruction. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 hidden md:block">
        <Particles
          className="opacity-85"
          particleCount={200}
          particleBaseSize={90}
          particleSpread={6}
          speed={0.06}
          particleColors={["#ffffff"]}
        />
      </div>

      {/* The graph rule (§4.4), fading in from the right so it never sits behind the form. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 right-0 hidden w-2/3 [background-image:var(--graph-rule)] [mask-image:linear-gradient(to_right,transparent,black_60%)] md:block"
      />

      {/* Fills what used to be a blank right column on desktop — hidden below `lg` (1024px) rather
          than `md` (768px) like the graph rule above, so it never fights the form for space at
          in-between widths. Sized off the viewport (matching the source export's own `min(78vmin,
          620px)`) rather than a fixed cap — a flat 420px left too much dead space around it once
          seen running against the actual size of this column. */}
      <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-1/2 items-center justify-center lg:flex">
        <VitalsFigure className="w-full max-w-[min(78vmin,620px)] opacity-90" />
      </div>

      <div className="relative flex min-h-dvh items-center px-6 md:px-[max(6vw,48px)]">
        <div className="w-full max-w-[380px]">
          <Link to="/" aria-label="AyuMind AI home" className="inline-block">
            <Logo size={20} animated={false} glowStrength={0} />
          </Link>

          <h1 className="mt-8 text-h2 text-foreground">{copy.title}</h1>

          <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-5" noValidate>
            <Field
              label="Email"
              type="email"
              name="email"
              autoComplete="email"
              autoFocus
              required
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setEmailError(undefined);
              }}
              error={emailError}
            />

            <Field
              label="Password"
              type={showPassword ? "text" : "password"}
              name="password"
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              required
              value={password}
              onBlur={() => setPasswordTouched(true)}
              onChange={(e) => {
                setPassword(e.target.value);
                setPasswordError(undefined);
              }}
              error={passwordError ?? (passwordTooShort ? "at least 8 characters" : undefined)}
              trailing={
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? (
                    <EyeOff className="size-4" strokeWidth={1.5} aria-hidden="true" />
                  ) : (
                    <Eye className="size-4" strokeWidth={1.5} aria-hidden="true" />
                  )}
                </Button>
              }
            />

            {formError && (
              <p role="alert" className="text-meta text-invalid">
                {formError}
              </p>
            )}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              isLoading={isSubmitting}
              className="w-full"
            >
              {copy.submit}
            </Button>
          </form>

          <p className="mt-6 text-dense text-muted-foreground">
            {copy.switchText}{" "}
            <Link
              to={copy.switchTo}
              className="text-foreground underline decoration-border underline-offset-4 transition-colors duration-120 hover:decoration-foreground"
            >
              {copy.switchLabel}
            </Link>
          </p>

          {/* Sets the ADR-13.4 expectation BEFORE the user reaches an empty app, and reframes a
              limitation as a statement of integrity. Signup only. */}
          {mode === "signup" && (
            <p className="mt-10 font-mono text-meta text-faint">
              every account starts empty — including yours
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
