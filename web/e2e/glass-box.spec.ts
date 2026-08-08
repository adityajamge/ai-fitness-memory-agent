/**
 * E2E path 2 of the Definition of Done: money question → chips → trace.
 *
 * This is the path judges are scored on. It asserts the chain the product exists to deliver:
 * an answer carries chips, a chip resolves to a real database row, and the row is reachable
 * along with the query that found it.
 *
 * Runs against a real API and a real CockroachDB. The question is an **aggregate**, chosen
 * deliberately: an aggregate's contributing memories are citable but never appear as evidence
 * snapshots (ADR-14.7), so this is the case where a naive implementation shows a chip with
 * nothing behind it.
 *
 * **Seeded once, serially, on a shared page.** Each turn here is a real model call taking
 * ~5-8s, so per-test seeding meant 18 of them and the suite timed out under its own load —
 * a test-design problem, not a product one. One account, one seed, assertions layered on top.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const uniqueEmail = () => `e2e-gb-${Date.now()}-${Math.random().toString(36).slice(2, 6)}@example.com`;

/** Sign up and log two meals, so an aggregate question has something to compute over. */
async function seedAccount(page: Page): Promise<void> {
  await page.goto("/signup");
  await page.getByRole("textbox", { name: "Email" }).fill(uniqueEmail());
  await page.getByRole("textbox", { name: "Password" }).fill("e2e-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/app$/);

  // Protein is stated explicitly. Extraction is a live model call, and asking it to *infer*
  // grams from "200g chicken" succeeded on some runs and not others — which made the aggregate
  // answer "no protein data logged" and the test flaky for a reason that has nothing to do with
  // the UI under test. Naming the number removes the stochastic step without weakening what is
  // being asserted: the chain from answer to chip to row to query.
  const meals = [
    "lunch today: grilled chicken and curd, 52g protein",
    "dinner today: 3 eggs and a protein shake, 40g protein",
  ];
  for (const [i, meal] of meals.entries()) {
    await page.locator("#composer").fill(meal);
    await page.getByRole("button", { name: "Send message" }).click();
    // Wait on the receipt COUNT, not on `.last()`: a previous meal's receipt is already on
    // screen, so `.last()` resolves immediately and the next message is sent before this one
    // has landed — which is how the aggregate ended up answering "no protein data logged".
    await expect(page.getByText(/memory created|parsing incomplete/)).toHaveCount(i + 1, {
      timeout: 60_000,
    });
  }
}

async function askAggregate(page: Page): Promise<void> {
  await page.locator("#composer").fill("how much protein did I eat today?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator('button[title^="citation resolves"]').first()).toBeVisible({
    timeout: 60_000,
  });
}

test.describe.configure({ mode: "serial" });

test.describe("glass box", () => {
  let context: BrowserContext;
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    // An explicit context, not `browser.newPage()`: AxeBuilder refuses a context-less page.
    context = await browser.newContext();
    page = await context.newPage();
    await seedAccount(page);
    await askAggregate(page);
  });

  test.afterAll(async () => {
    await context.close();
  });

  test("an answer carries chips that resolve to real rows", async () => {
    const chip = page.locator('button[title^="citation resolves"]').first();
    // The chip's label comes from the hydrated row, never from parsing narration (ADR-12).
    await expect(chip).toHaveAttribute("title", /citation resolves to memory/);
    // Honest scope (ADR-13.13): "resolves", never "verified".
    await expect(chip).not.toHaveAttribute("title", /verified/);
  });

  test("clicking a chip highlights its evidence row", async () => {
    const chip = page.locator('button[title^="citation resolves"]').first();
    await expect(chip).toHaveAttribute("aria-pressed", "false");
    await chip.click();
    await expect(chip).toHaveAttribute("aria-pressed", "true");

    // The receiving half of the choreography: the matching row takes the signal border.
    const pane = page.locator('aside[aria-label="Memory engine"]');
    await expect(pane.locator("li.border-signal")).toHaveCount(1);
  });

  test("the executed query is shown, not described", async () => {
    await page.getByRole("button", { name: /how this was retrieved/ }).click();
    const sql = page.locator('aside[aria-label="Memory engine"] pre code').first();
    // The real aggregation, with its bound parameters — served verbatim from the trace (I-29).
    await expect(sql).toContainText("FROM memories");
    await expect(sql).toContainText("SUM");
  });

  test("an aggregate's contributing row appears even though it is not an evidence snapshot", async () => {
    // ADR-14.7: assembly is pure, so this row is citable but has no snapshot. Rendering only
    // `trace.evidence` would leave the chip with nothing to point at.
    const pane = page.locator('aside[aria-label="Memory engine"]');
    await expect(pane.locator("li")).not.toHaveCount(0);
    await expect(pane).not.toContainText("no context assembled");
  });

  test("history stays inspectable after a reload", async () => {
    await page.reload();

    // Without per-turn trace fetching this is where every past answer goes inert.
    const chip = page.locator('button[title^="citation resolves"]').first();
    await expect(chip).toBeVisible({ timeout: 30_000 });
    await chip.click();
    await expect(
      page.locator('aside[aria-label="Memory engine"] li.border-signal'),
    ).toHaveCount(1);
  });

  test("the glass box has no accessibility violations", async () => {
    await expect(page.locator('button[title^="citation resolves"]').first()).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();

    expect(results.violations).toEqual([]);
  });
});
