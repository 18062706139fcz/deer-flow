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

  test("can install the Lark integration skill pack from settings", async ({
    page,
  }) => {
    mockLangGraphAPI(page);

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
    await expect(
      dialog.getByText("https://open.feishu.cn/auth/mock-device"),
    ).toBeVisible();

    await dialog
      .getByRole("button", { name: "I completed authorization" })
      .click();
    await expect(
      page.getByText("Lark/Feishu authorization completed."),
    ).toBeVisible();
  });
});
