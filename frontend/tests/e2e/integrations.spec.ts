import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.describe("Integrations settings", () => {
  test("opens integrations settings from a query-string deep link", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new?settings=integrations");

    const dialog = page.getByRole("dialog", { name: "Settings" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Lark / Feishu CLI")).toBeVisible();
  });

  test("keeps a single settings dialog across deep link and nav menu openings", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

    // Deep link opens the shared dialog on Integrations.
    await page.goto("/workspace/chats/new?settings=integrations");
    const dialog = page.getByRole("dialog", { name: "Settings" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Lark / Feishu CLI")).toBeVisible();
    await expect(page.getByRole("dialog", { name: "Settings" })).toHaveCount(1);

    // Close the modal before using the sidebar. While the modal is open, the
    // background is intentionally inert and Playwright should not be able to
    // click sidebar controls there.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Settings" })).toHaveCount(0);

    // Opening again from the nav menu must still use the same shared host, not
    // mount a second SettingsDialog instance.
    const sidebar = page.locator("[data-sidebar='sidebar']");
    await sidebar.getByRole("button", { name: /Settings and more/ }).click();
    await page.getByRole("menuitem", { name: "Settings" }).click();

    // Exactly one Settings dialog is mounted/visible at any time.
    await expect(page.getByRole("dialog", { name: "Settings" })).toHaveCount(1);
  });

  test("can install the Lark integration skill pack from settings", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    let authStartRequest: unknown;
    await page.route("**/api/integrations/lark/auth/start", async (route) => {
      authStartRequest = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          verification_url: "https://open.feishu.cn/auth/mock-device",
          device_code: "mock-device-code",
          expires_in: 600,
          user_code: null,
          hint: null,
        }),
      });
    });

    await page.goto("/workspace/chats/new");

    const sidebar = page.locator("[data-sidebar='sidebar']");
    await sidebar.getByRole("button", { name: /Settings and more/ }).click();
    await page.getByRole("menuitem", { name: "Settings" }).click();

    const dialog = page.getByRole("dialog", { name: "Settings" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Integrations" }).click();

    await expect(dialog.getByText("Lark / Feishu CLI")).toBeVisible();
    await expect(
      dialog.getByText("Install the official skill pack first"),
    ).toBeVisible();

    await dialog.getByRole("button", { name: "Install" }).click();
    await expect(
      page.getByText("Installed 3 Lark/Feishu skills."),
    ).toBeVisible();

    await dialog.getByRole("button", { name: "Calendar" }).click();
    await dialog
      .getByLabel("Exact OAuth scope")
      .fill("calendar:calendar.event:read");
    await dialog.getByRole("button", { name: "Connect Lark" }).click();
    await expect(
      dialog.getByText("https://open.feishu.cn/page/cli?user_code=config"),
    ).toBeVisible();
    await expect(dialog.getByText(/app configuration/i)).toHaveCount(0);

    await dialog
      .getByRole("button", {
        name: "I completed browser confirmation, continue",
      })
      .click();
    await expect
      .poll(() => authStartRequest)
      .toMatchObject({
        recommend: false,
        domains: ["calendar"],
        scope: "calendar:calendar.event:read",
      });

    await expect(
      page.getByText("Lark/Feishu authorization completed."),
    ).toBeVisible();
    await expect(dialog.getByText("Lark is connected")).toBeVisible();
    await expect(
      page.getByText("Authorization page opened. Waiting for completion..."),
    ).toHaveCount(0);

    await dialog.getByRole("button", { name: "Calendar" }).click();
    await dialog.getByLabel("Exact OAuth scope").fill("");
    await dialog.getByRole("button", { name: "Connected" }).click();
    await expect(page.getByText(/Lark is already connected/)).toBeVisible();
    await expect(
      dialog.getByText("https://open.feishu.cn/auth/mock-device"),
    ).toHaveCount(0);
  });
});
