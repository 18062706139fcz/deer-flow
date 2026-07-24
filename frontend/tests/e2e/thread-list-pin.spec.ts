import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const NEWEST_THREAD_ID = "00000000-0000-0000-0000-000000000901";
const OLDER_THREAD_ID = "00000000-0000-0000-0000-000000000902";

async function recentChatTitles(page: Page) {
  return page
    .locator('a[data-sidebar="menu-button"][href^="/workspace/chats/"]')
    .evaluateAll((links) =>
      links
        .map((link) => link.textContent?.replace(/\s+/g, " ").trim() ?? "")
        .filter((text) => text && text !== "New chat"),
    );
}

test("sidebar recent chats can be pinned and unpinned", async ({ page }) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: NEWEST_THREAD_ID,
        title: "Newest chat",
        updated_at: "2026-07-04T10:00:00Z",
      },
      {
        thread_id: OLDER_THREAD_ID,
        title: "Older chat",
        updated_at: "2026-07-03T10:00:00Z",
      },
    ],
  });

  await page.goto("/workspace/chats/new");

  await expect(page.getByText("Newest chat")).toBeVisible({ timeout: 15_000 });
  await expect
    .poll(() => recentChatTitles(page))
    .toEqual(["Newest chat", "Older chat"]);

  const olderItem = page
    .locator(
      `a[data-sidebar="menu-button"][href="/workspace/chats/${OLDER_THREAD_ID}"]`,
    )
    .locator("xpath=..");
  await olderItem.hover();
  await olderItem.getByRole("button", { name: "More" }).click();
  await page.getByRole("menuitem", { name: "Pin chat" }).click();

  await expect
    .poll(() => recentChatTitles(page))
    .toEqual(["Older chat", "Newest chat"]);

  await olderItem.hover();
  await olderItem.getByRole("button", { name: "More" }).click();
  await page.getByRole("menuitem", { name: "Unpin chat" }).click();

  await expect
    .poll(() => recentChatTitles(page))
    .toEqual(["Newest chat", "Older chat"]);
});
