import { AsyncLocalStorage } from "node:async_hooks";
import type { OrganizationId } from "@tac/application";
import { sql } from "drizzle-orm";
import type { Db, Tx } from "./client.js";

/**
 * テナントの文脈つきでクエリを流す（INV-2 の DB 層）。
 * すべてのクエリは次の状態のトランザクションの中で実行する：
 * 1. `SET LOCAL ROLE tac_app` … RLS の対象になるロールに切り替える（所有者・スーパーユーザーで接続していても RLS を外さない）
 * 2. `set_config('app.org_id', 組織, true)` … RLS ポリシーが参照する組織。未設定なら行は1件も見えない
 * どちらもトランザクション終了で元に戻るので、接続プールで次の利用者に漏れない。
 *
 * UnitOfWork の中では、AsyncLocalStorage でそのトランザクションを共有する
 * （PGlite はトランザクション中に外のクエリを待たせるため、共有しないと自分自身を待って止まる）。
 * 1つのトランザクションは1つの組織だけを扱う。途中で別の組織を指定したら例外にする。
 */

export class TenantMismatchError extends Error {
  constructor() {
    super("a transaction cannot span multiple organizations");
    this.name = "TenantMismatchError";
  }
}

export class InvalidOrganizationIdError extends Error {
  constructor() {
    super("organization id must be a UUID");
    this.name = "InvalidOrganizationIdError";
  }
}

interface Ambient {
  readonly db: Db;
  readonly tx: Tx;
  org: OrganizationId | undefined;
  bound: Promise<unknown>;
}

const ambient = new AsyncLocalStorage<Ambient>();

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (value: string) => UUID.test(value);

export class TenantScope {
  constructor(readonly db: Db) {}

  /** 組織の文脈でクエリを流す。org を省略するとテナントのない文脈（system_controls を読むだけ） */
  async withTenant<T>(org: OrganizationId | undefined, work: (tx: Tx) => Promise<T>): Promise<T> {
    const current = this.current();
    if (current) {
      if (org !== undefined) await bindOrganization(current, org);
      return work(current.tx);
    }
    return this.db.transaction(async (tx) => {
      const state: Ambient = { db: this.db, tx, org: undefined, bound: Promise.resolve() };
      await tx.execute(sql`set local role tac_app`);
      if (org !== undefined) await bindOrganization(state, org);
      return ambient.run(state, () => work(tx));
    });
  }

  /** 複数の書き込みを1つのトランザクションにまとめる（UnitOfWork.run） */
  run<T>(work: () => Promise<T>): Promise<T> {
    if (this.current()) return work();
    return this.withTenant(undefined, () => work());
  }

  /**
   * 組織単位で直列化したトランザクション（UnitOfWork.runExclusive）。
   * 組織 ID から作ったキーの advisory lock を取り、コミット / ロールバックで解放する。
   */
  runExclusive<T>(org: OrganizationId, work: () => Promise<T>): Promise<T> {
    return this.withTenant(org, async (tx) => {
      await tx.execute(sql`select pg_advisory_xact_lock(hashtextextended(${`tac:org:${org}`}, 0))`);
      return work();
    });
  }

  private current(): Ambient | undefined {
    const store = ambient.getStore();
    return store?.db === this.db ? store : undefined;
  }
}

async function bindOrganization(state: Ambient, org: OrganizationId): Promise<void> {
  if (!isUuid(org)) throw new InvalidOrganizationIdError();
  if (state.org === undefined) {
    // 同じトランザクションで並行に呼ばれても、組織の設定は1回だけ。後続は設定の完了を待ってからクエリを流す
    state.org = org;
    state.bound = Promise.resolve(
      state.tx.execute(sql`select set_config('app.org_id', ${org}, true)`),
    );
  } else if (state.org !== org) {
    throw new TenantMismatchError();
  }
  await state.bound;
}
