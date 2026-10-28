import { test, expect } from "@playwright/test";

const launch = "/#token=browser-test-token";

test("fresh browser displays the app with incorrect OS MIME associations", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(launch);
  await expect(page.getByRole("button", { name: "比較を開始" })).toBeVisible();
  await expect(page.locator("#startup-status")).toBeHidden();
  await expect(page).toHaveURL(/:\d+\/$/);
  await page.reload();
  await expect(page.getByRole("button", { name: "比較を開始" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("blocked browser storage/history cannot prevent startup or reload", async ({
  page,
}) => {
  await page.addInitScript(() => {
    for (const method of ["getItem", "setItem"] as const) {
      Storage.prototype[method] = () => {
        throw new DOMException("Browser storage blocked", "SecurityError");
      };
    }
    history.replaceState = () => {
      throw new DOMException("History blocked", "SecurityError");
    };
  });
  await page.goto(launch);
  await expect(page.getByRole("button", { name: "比較を開始" })).toBeVisible();
  await expect(page).toHaveURL(/#token=browser-test-token$/);
  await expect(page.locator("#startup-status")).toBeHidden();
  await page.reload();
  await expect(page.getByRole("button", { name: "比較を開始" })).toBeVisible();
  expect(
    await page.evaluate(async () => {
      const token = new URLSearchParams(location.hash.slice(1)).get("token")!;
      return (
        await fetch("/api/health", { headers: { "X-Pawapo-Token": token } })
      ).status;
    }),
  ).toBe(200);
});

test("missing module shows diagnostics rather than a blank screen", async ({
  page,
}) => {
  await page.route("**/assets/*.js", (route) =>
    route.fulfill({ status: 404, body: "missing" }),
  );
  await page.goto(launch);
  await expect(
    page.getByRole("heading", { name: "画面を起動できませんでした" }),
  ).toBeVisible();
  await expect(page.locator("#startup-detail")).toContainText(
    "UI_ASSET_LOAD_FAILED",
  );
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "画面の診断テキストを保存" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("PawapoDiff-startup.txt");
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(chunk);
  const diagnostic = Buffer.concat(chunks).toString("utf8");
  expect(diagnostic).toContain("UI_ASSET_LOAD_FAILED");
  expect(diagnostic).not.toContain("browser-test-token");
});

test("module exception is displayed without exposing the launch token", async ({
  page,
}) => {
  await page.route("**/assets/*.js", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/javascript",
      body: "throw new Error('Startup failed at ' + location.href)",
    }),
  );
  await page.goto(launch);
  await expect(
    page.getByRole("heading", { name: "画面を起動できませんでした" }),
  ).toBeVisible();
  await expect(page.locator("#startup-detail")).toContainText(
    "UI_SCRIPT_FAILED",
  );
  await expect(page.locator("#startup-detail")).not.toContainText(
    "browser-test-token",
  );
});

test("missing launch credentials produce a visible error", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByText("起動時の認証情報がありません。", { exact: false }),
  ).toBeVisible();
  await expect(page.locator("#startup-status")).toBeHidden();
});

test("blocked history changes do not crash an otherwise normal browser", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    history.replaceState = () => {
      throw new DOMException("History blocked", "SecurityError");
    };
  });
  await page.goto(launch);
  await expect(page.getByRole("button", { name: "比較を開始" })).toBeVisible();
  await page.reload();
  await expect(page.locator("#startup-status")).toBeHidden();
  expect(errors).toEqual([]);
});

test("an app that never mounts times out with visible instructions", async ({
  page,
}) => {
  await page.clock.install();
  await page.route("**/assets/*.js", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/javascript",
      body: "/* no mounted app */",
    }),
  );
  await page.goto(launch);
  await page.clock.runFor(10001);
  await expect(page.locator("#startup-detail")).toContainText(
    "UI_START_TIMEOUT",
  );
  await expect(
    page.getByRole("button", { name: "画面の診断テキストを保存" }),
  ).toBeVisible();
});

test("JavaScript disabled still leaves readable HTML instructions", async ({
  browser,
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto("http://127.0.0.1:8784" + launch);
    await expect(page.locator("noscript")).toBeVisible();
    expect(await page.locator("noscript").textContent()).toContain("JavaScriptが無効のため画面を開けません。");
  } finally {
    await context.close();
  }
});
