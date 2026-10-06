import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, openLead } from "./support";

/*
 * SCREEN_SPEC §11 の E2E（AI・引き継ぎは Phase 12・13 のため除く）。
 * 状態の進み方は API のシミュレーターが Webhook を自動で届けることで再現する。
 */

test("1. ログイン → リード → 発信 → 状態が終話まで進む → 結果を記録 → 次の相手へ", async ({
  page,
}) => {
  await login(page);
  await openLead(page, 0);
  await page.getByRole("button", { name: "発信する" }).click();
  const status = page.getByTestId("call-status");
  await expect(status).toBeVisible();
  // 文字ラベルで状態が分かる（色だけにしない）。シミュレーターの ANSWER → 終話まで進む
  await expect(status).toHaveText(/通話終了/, { timeout: 15_000 });
  await page.getByRole("button", { name: "検討" }).click();
  await page.getByRole("button", { name: "結果を保存" }).click();
  await expect(page.getByText("『検討』で記録しました")).toBeVisible();
  await page.getByRole("link", { name: "次の相手へ" }).click();
  await expect(page.getByRole("heading", { name: "リード" })).toBeVisible();
});

test("2. 拒否 → 発信禁止の表示 → 発信ボタンが消える → API に直接送っても拒否される", async ({
  page,
}) => {
  await login(page);
  await openLead(page, 1);
  const contactUrl = page.url();
  await page.getByRole("button", { name: "発信する" }).click();
  await expect(page.getByTestId("call-status")).toHaveText(/通話終了/, { timeout: 15_000 });
  await page.getByRole("button", { name: "拒否" }).click();
  await expect(page.getByRole("note")).toContainText("発信禁止になります");
  await page.getByRole("button", { name: "発信禁止にして保存" }).click();
  await expect(page.getByText("『拒否』で記録しました")).toBeVisible();

  await page.goto(contactUrl);
  await expect(page.getByTestId("suppression-banner")).toContainText("発信禁止");
  await expect(page.getByRole("button", { name: "発信する" })).toHaveCount(0);

  // 画面を経由せずに API へ直接送っても、サーバーが拒否する（画面の判断は表示だけ）
  const csrf = await page.evaluate(() => sessionStorage.getItem("tac.csrf") ?? "");
  const contactId = contactUrl.split("/").pop() ?? "";
  const campaigns = await (await page.request.get("/v1/campaigns")).json();
  const res = await page.request.post("/v1/calls", {
    headers: { "x-csrf-token": csrf, "idempotency-key": `direct-${Date.now()}` },
    data: { contactId, campaignId: campaigns.items[0].id, mode: "HUMAN_DIALED" },
  });
  expect(res.status()).toBe(422);
  expect((await res.json()).error.code).toBe("CONTACT_SUPPRESSED");
});

test("3. 発信ボタンを連打しても、送られる発信の要求は 1 件", async ({ page }) => {
  await login(page);
  await openLead(page, 2);
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/v1/calls")) posts.push(r.url());
  });
  const button = page.getByRole("button", { name: "発信する" });
  await button.click({ clickCount: 5, delay: 10 });
  await expect(page.getByTestId("call-status")).toBeVisible();
  await page.waitForTimeout(500);
  expect(posts).toHaveLength(1);
});

test("6. アクセシビリティ：ログイン・リード・Call Workspace で重大な違反 0（axe）", async ({
  page,
}) => {
  const serious = async () =>
    (
      await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze()
    ).violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  await page.goto("/login");
  expect(await serious()).toEqual([]);
  await login(page);
  expect(await serious()).toEqual([]);
  await openLead(page, 3);
  await expect(page.getByRole("button", { name: "発信する" })).toBeVisible();
  expect(await serious()).toEqual([]);
});

test("6. キーボードだけでログインして、相手を開いて発信できる", async ({ page }) => {
  await page.goto("/login");
  await page.keyboard.press("Tab");
  await page.keyboard.type("operator@example.test");
  await page.keyboard.press("Tab");
  await page.keyboard.type("e2e-demo-password-123");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "リード" })).toBeVisible();
  const link = page.getByRole("link", { name: /^開く/ }).nth(4);
  await link.focus();
  await page.keyboard.press("Enter");
  const call = page.getByRole("button", { name: "発信する" });
  await call.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("call-status")).toBeVisible();
});

test("未ログインで画面を開くとログインへ", async ({ page }) => {
  await page.goto("/leads");
  await expect(page).toHaveURL(/\/login$/);
});
