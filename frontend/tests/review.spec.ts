import { test, expect, Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
const folder = path.resolve("test-results/demo-input");
const python =
  process.platform === "win32"
    ? path.resolve("../.venv/Scripts/python.exe")
    : path.resolve("../.venv/bin/python");
test.beforeAll(() => {
  execFileSync(python, [path.resolve("../scripts/create-demo.py"), folder]);
  execFileSync(python, [
    "-c",
    `from pptx import Presentation\nfrom pptx.util import Pt\np=Presentation(r'${folder}/demo-en.pptx')\np.slides[0].shapes[0].left+=Pt(4)\np.slides[0].shapes[2].text_frame.paragraphs[0].runs[0].font.bold=True\np.slides[0].shapes[3].left+=Pt(4)\np.slides[0].shapes[3].text_frame.paragraphs[0].runs[0].font.bold=True\np.save(r'${folder}/demo-en.pptx')`,
  ]);
});
async function upload(page: Page, mode: string) {
  await page.goto("/#token=browser-test-token");
  await page.getByRole("button", { name: mode, exact: true }).click();
  await page
    .locator("input[type=file]")
    .nth(0)
    .setInputFiles(path.join(folder, "demo-ja.pptx"));
  await page
    .locator("input[type=file]")
    .nth(1)
    .setInputFiles(path.join(folder, "demo-en.pptx"));
  await page.getByRole("button", { name: "比較を開始" }).click();
  await expect(page.getByRole("button", { name: "比較を開始" })).toBeEnabled({
    timeout: 30000,
  });
}

test("real PPTX in Web consistency view; CSV download; LLM protocols and custom prompts", async ({
  page,
}) => {
  await upload(page, "表記ゆれ検知");
  await expect(
    page.getByRole("heading", { name: "表記ゆれ検知", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Sales", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", {
      name: "Revenue grew year over year.",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "画像を生成してページ確認", exact: true })
    .click();
  await expect(
    page.getByRole("img", { name: "JPスライド 1ページ", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "ENスライド 1ページ", exact: true }),
  ).toBeVisible();
  let releaseImages!: () => void;
  const imageGate = new Promise<void>((resolve) => {
    releaseImages = resolve;
  });
  await page.route(/\/images\/(left|right|diff)\/2$/, async (route) => {
    await imageGate;
    await route.continue();
  });
  await page.getByRole("button", { name: "次のページ", exact: true }).click();
  // A new page must not temporarily reuse the previous page's blob images.
  await expect(
    page.getByRole("img", { name: "JPスライド 1ページ", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("画像を読み込み中…", { exact: true }),
  ).toHaveCount(2);
  releaseImages();
  await expect(
    page.getByRole("cell", { name: "Revenue", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "JPスライド 2ページ", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "ENスライド 2ページ", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".notes-pair")).toContainText(
    "Sales increased year on year.",
  );
  await expect(page.locator(".notes-pair")).not.toContainText(
    "Revenue grew year over year.",
  );
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", {
      name: "発表者ノート比較をダウンロード",
      exact: true,
    })
    .click();
  expect((await download).suggestedFilename()).toBe("speaker-notes.csv");
  await page.screenshot({
    path: "test-results/consistency-web.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "LLMレビュー", exact: true }).click();
  const guideDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Ollamaの動作手順を保存", exact: true })
    .click();
  expect((await guideDownload).suggestedFilename()).toBe("Ollama-guide.md");
  const diagnosticDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "診断ログを保存", exact: true })
    .click();
  expect((await diagnosticDownload).suggestedFilename()).toBe(
    "PawapoDiff-diagnostics.zip",
  );

  await page
    .getByLabel("接続先URL", { exact: true })
    .fill("http://127.0.0.1:8786");
  await page.getByRole("button", { name: "モデル一覧・接続確認" }).click();
  await expect(page.getByLabel("モデル名", { exact: true })).toHaveValue(
    "fixture-vision",
  );
  await page.getByRole("button", { name: "モデルの画像対応を確認" }).click();
  await expect(
    page.getByLabel("選択したモデルは画像入力に対応している"),
  ).toBeChecked();
  await page.getByLabel("出力トークン上限", { exact: true }).fill("8192000");
  await page.getByLabel("応答待ち上限（秒）", { exact: true }).fill("86400");
  await page.getByText("既定／カスタムプロンプト", { exact: true }).click();
  // Fill the task-specific prompt without changing the task checkbox.
  await page
    .locator("details")
    .filter({
      has: page.locator(":scope > summary", {
        hasText: "既定／カスタムプロンプト",
      }),
    })
    .locator("textarea")
    .nth(1)
    .fill("カスタム用語検査。根拠とページ番号を提示。");
  await page
    .getByRole("button", { name: "LLM検査を開始", exact: true })
    .click();
  await expect(page.getByText("done: 完了", { exact: false })).toBeVisible({
    timeout: 20000,
  });
  await expect(
    page.getByText("API接続テスト応答（実LLMではありません）").first(),
  ).toBeVisible();
  const currentResults = page.locator(".page-workspace .review-result");
  await expect(currentResults).toHaveCount(2);
  await expect(page.locator(".page-workspace")).toContainText(
    "発表者ノート比較 1ページ",
  );
  await expect(page.locator(".page-workspace")).not.toContainText(
    "発表者ノート比較 2ページ",
  );
  await page.getByRole("button", { name: "次のページ", exact: true }).click();
  await expect(page.locator(".page-workspace")).toContainText(
    "発表者ノート比較 2ページ",
  );
  await expect(page.locator(".page-workspace")).not.toContainText(
    "発表者ノート比較 1ページ",
  );
  await expect(page.locator(".notes-pair")).toContainText(
    "Sales increased year on year.",
  );
  await page
    .getByRole("button", { name: "全体・一貫性レビュー（1件）", exact: true })
    .click();
  await expect(
    page
      .getByRole("region", { name: "全体レビュー", exact: true })
      .locator(".review-result"),
  ).toHaveCount(1);
  await expect(page.locator(".page-workspace")).toHaveCount(0);
  await page
    .getByRole("button", { name: "ページ別レビュー", exact: true })
    .click();
  const resultDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "結果・使用プロンプトを保存" })
    .click();
  const saved = await resultDownload;
  expect(saved.suggestedFilename()).toBe("llm-review.json");
  const stream = await saved.createReadStream();
  const buffers: Buffer[] = [];
  for await (const chunk of stream!) buffers.push(chunk);
  const review = JSON.parse(Buffer.concat(buffers).toString("utf-8"));
  expect(review.max_tokens).toBe(8192000);
  expect(
    review.results
      .filter((r: any) => r.task === "translation")
      .map((r: any) => r.page),
  ).toEqual([1, 2, 3, 4]);
  expect(
    review.results
      .filter((r: any) => r.task === "notes")
      .map((r: any) => r.page),
  ).toEqual([1, 2, 3, 4]);
  await page.screenshot({
    path: "test-results/llm-review-web.png",
    fullPage: true,
  });
});

test("diff categories and manual mapping update the real comparison; notes are download-only", async ({
  page,
}) => {
  await upload(page, "差分比較");
  await expect(
    page.getByRole("heading", { name: "発表者ノート・日英比較" }),
  ).toHaveCount(0);
  await expect(page.locator(".changes .badge.format")).toHaveCount(1);
  await expect(page.locator(".changes .badge.layout")).toHaveCount(1);
  await page.getByLabel("書式のみ", { exact: true }).uncheck();
  await expect(page.locator(".changes .badge.format")).toHaveCount(0);
  await expect(page.locator(".changes .badge.layout")).toHaveCount(1);
  await page.getByLabel("位置・サイズ・回転のみ", { exact: true }).uncheck();
  await expect(page.locator(".changes .badge.layout")).toHaveCount(0);
  await expect(page.locator(".changes .badge.mixed")).toHaveCount(1);
  await page
    .getByText("このページの対応付けを手動設定", { exact: true })
    .click();
  await page.getByLabel("対応先 3", { exact: true }).selectOption("__none");
  await page.getByRole("button", { name: "対応付けを適用" }).click();
  await expect(page.getByLabel("対応先 3", { exact: true })).toHaveValue(
    "__none",
  );
  await expect(
    page.getByText("プレースホルダー削除", { exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "このページを自動判定に戻す" })
    .click();
  await expect(page.getByLabel("対応先 3", { exact: true })).toHaveValue(
    "__auto",
  );
  await page.screenshot({
    path: "test-results/diff-mapping-web.png",
    fullPage: true,
  });
});

test("consistency image generation errors are visible and can be retried", async ({
  page,
}) => {
  await upload(page, "表記ゆれ検知");
  await page.route(/\/api\/jobs\/[^/]+\/render$/, (route) =>
    route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ detail: "PowerPointを起動できませんでした" }),
    }),
  );
  await page
    .getByRole("button", { name: "画像を生成してページ確認", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "PowerPointを起動できませんでした",
  );
  await expect(
    page.getByRole("button", { name: "画像を生成してページ確認", exact: true }),
  ).toBeEnabled();
});
