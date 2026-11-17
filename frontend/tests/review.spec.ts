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
    page.getByRole("cell", { name: "Revenue", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", {
      name: "Revenue grew year over year.",
      exact: true,
    }),
  ).toBeVisible();
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
  await page.getByText("既定／カスタムプロンプト", { exact: true }).click();
  // Fill the task-specific prompt without changing the task checkbox.
  await page
    .locator("details")
    .filter({
      has: page.locator("summary", { hasText: "既定／カスタムプロンプト" }),
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
  const resultDownload = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "結果・使用プロンプトを保存" })
    .click();
  expect((await resultDownload).suggestedFilename()).toBe("llm-review.json");
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
