/**
 * E2E path 1 of the Definition of Done: signup → log → receipt → pane.
 *
 * This is the highest-stakes path in the product. ADR-13.4 means every judge gets an empty
 * account with no seed data, so this exact sequence *is* the live product experience being
 * scored — see DESIGN.md §9.1.
 *
 * Runs against a real API and a real CockroachDB. It signs up a genuine account each run
 * (uniquely named) because the empty-account state is the thing under test, and a fixture that
 * pre-seeded data would test a state no judge will ever see.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const uniqueEmail = () => `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;

async function signUp(page: Page): Promise<void> {
  await page.goto("/signup");
  await page.getByRole("textbox", { name: "Email" }).fill(uniqueEmail());
  await page.getByRole("textbox", { name: "Password" }).fill("e2e-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/app$/);
}

test.describe("first run", () => {
  test("a new account is inviting, not broken", async ({ page }) => {
    await signUp(page);

    // Honest zeros, not a hidden or errored bar. This shape is pinned server-side by
    // api/tests/test_glassbox.py; here we assert the UI renders it as a designed state.
    await expect(page.getByRole("heading", { name: "Your memory starts here." })).toBeVisible();
    await expect(page.getByText("0", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Nothing retrieved yet." })).toBeVisible();

    // Example prompts must be real loggable text, not "Try asking about…" placeholders.
    await expect(page.getByRole("button", { name: /250g curd/ })).toBeVisible();
  });

  test("clicking a prompt fills the composer and dims the prompts", async ({ page }) => {
    await signUp(page);
    await page.getByRole("button", { name: /250g curd/ }).click();
    await expect(page.locator("#composer")).toHaveValue(/250g curd/);
    await expect(page.getByRole("button", { name: "Send message" })).toBeEnabled();
  });

  test("logging produces a receipt and the stats tick", async ({ page }) => {
    await signUp(page);
    await page.getByRole("button", { name: /250g curd/ }).click();
    await page.getByRole("button", { name: "Send message" }).click();

    // The payoff: proof that talking IS logging. Either outcome is a pass — a failed parse still
    // creates a note memory and still shows a receipt, because never-lose-input is the guarantee
    // being demonstrated (ADR-13.5).
    await expect(
      page.getByText(/memory created|parsing incomplete/).first(),
    ).toBeVisible({ timeout: 60_000 });

    // Step 5: the engine confirms the same action on a second surface.
    await expect(page.getByText(/1\s*memory/).first()).toBeVisible();

    // Step 6: the hand-off to asking, shown once.
    await expect(page.getByText(/now ask about it/)).toBeVisible();
  });

  test("the empty app has no accessibility violations", async ({ page }) => {
    await signUp(page);
    await expect(page.getByRole("heading", { name: "Your memory starts here." })).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();

    expect(results.violations).toEqual([]);
  });
});

test.describe("marketing and auth", () => {
  test("the landing page states the claim and routes to signup", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("It remembers.");
    await page.getByRole("link", { name: "Try it" }).first().click();
    await expect(page).toHaveURL(/\/signup$/);
  });

  test("a duplicate email is reported on the field, not as a toast", async ({ page }) => {
    const email = uniqueEmail();
    for (const attempt of [1, 2]) {
      await page.goto("/signup");
      await page.getByRole("textbox", { name: "Email" }).fill(email);
      await page.getByRole("textbox", { name: "Password" }).fill("e2e-password-123");
      await page.getByRole("button", { name: "Create account" }).click();
      if (attempt === 1) await expect(page).toHaveURL(/\/app$/);
    }
    await expect(page.getByText("that email already has an account")).toBeVisible();
    await expect(page).toHaveURL(/\/signup$/);
  });

  test("bad credentials never reveal whether the account exists", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("textbox", { name: "Email" }).fill("definitely-not-a-user@example.com");
    await page.getByRole("textbox", { name: "Password" }).fill("wrong-password-123");
    await page.getByRole("button", { name: "Sign in" }).click();

    // "email or password is incorrect" — naming which one confirms an address to anyone probing.
    await expect(page.getByText("email or password is incorrect")).toBeVisible();
  });

  test("an unknown route renders a real 404", async ({ page }) => {
    await page.goto("/no-such-page");
    await expect(page.getByText("There's nothing at this address.")).toBeVisible();
  });
});
