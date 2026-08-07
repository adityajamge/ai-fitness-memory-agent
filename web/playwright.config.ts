import { defineConfig, devices } from "@playwright/test";

/**
 * E2E config.
 *
 * Points at an already-running dev stack rather than starting one, because the API needs a real
 * CockroachDB and the credentials for it live outside this directory. `E2E_BASE_URL` lets CI aim
 * at the built container instead of Vite.
 *
 * The four Definition-of-Done paths land across M4–M8; `first-run.spec.ts` is path 1.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // each spec signs up a real account against a real cluster
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "line" : "list",
  timeout: 90_000, // a live LLM turn against a cross-region cluster is genuinely slow
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    colorScheme: "dark",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
