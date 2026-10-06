import { expect, type Page } from "@playwright/test";
import { DEMO_PASSWORD } from "../playwright.config";

export const DEMO_EMAIL = "operator@example.test";

export async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("メールアドレス").fill(DEMO_EMAIL);
  await page.getByLabel("パスワード").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("heading", { name: "リード" })).toBeVisible();
}

/** リードの n 番目（名前順）の Call Workspace を開く。テストごとに別の相手を使う（状態を持ち越さない） */
export async function openLead(page: Page, index: number) {
  const links = page.getByRole("link", { name: /^開く/ });
  await expect(links.first()).toBeVisible();
  await links.nth(index).click();
  await expect(page.getByRole("heading", { name: "発信" })).toBeVisible();
}
