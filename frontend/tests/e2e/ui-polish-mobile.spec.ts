import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

test.describe("UI polish mobile regressions", () => {
  test("workspace exposes mobile sidebar navigation from the chat header", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new");

    await page.getByRole("button", { name: /toggle sidebar/i }).click();

    await expect(page.getByRole("link", { name: /new chat/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /agents/i })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(375);
  });

  test("mobile artifacts open in a drawer without horizontal overflow", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          title: "Thread with artifact",
          artifacts: ["reports/mobile-summary.md"],
        },
      ],
    });

    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await page.getByRole("button", { name: /artifacts/i }).click();

    await expect(page.getByRole("dialog", { name: /artifacts/i })).toBeVisible();
    await expect(page.getByText("mobile-summary.md")).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(375);
  });

  test("global focus ring tokens are visible in light and dark themes", async ({
    page,
  }) => {
    mockLangGraphAPI(page);
    await page.goto("/workspace/chats/new");

    const lightRing = await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--ring")
        .trim(),
    );
    expect(lightRing).not.toBe("transparent");

    await page.evaluate(() => document.documentElement.classList.add("dark"));

    const darkRing = await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--ring")
        .trim(),
    );
    expect(darkRing).not.toBe("transparent");
  });
});
