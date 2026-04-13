import { expect, test } from "@playwright/test";

test("user can create a widget and see it in the list", async ({ page }) => {
  await page.goto("/en/widgets");

  // Fill the form
  await page.getByLabel(/name/i).fill("Test Widget");
  await page.getByLabel(/description/i).fill("A test widget");
  await page.getByRole("button", { name: /create/i }).click();

  // Assert the widget appears in the list
  await expect(page.getByText("Test Widget")).toBeVisible();
  await expect(page.getByText("A test widget")).toBeVisible();
});
